"""Continuous, fair activation around the guarded field-effect kernel."""

from __future__ import annotations

import json
import threading
import time
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Callable, Protocol, cast

from mechanical_librarian.application.field_effects import (
    AuthorityCurrentness,
    CapabilityObserver,
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.application.operation_admission import (
    OperationAdmission,
    WorkClass,
)
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.domain.activity_records import (
    ActivityProjectionError,
    ReadableActivityEvent,
    project_readable_activity_event,
)
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    qualification_manifest_is_current,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    CoverAutomaticDecision,
    parse_automatic_authority,
)
from mechanical_librarian.domain.description_advice import AuthoredDescriptionDecision
from mechanical_librarian.domain.field_effect import (
    SUPPORTED_DESTINATIONS,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    MAX_REPORT_BYTES,
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.provider_recovery import RECOVERY_FAILURES


@dataclass(frozen=True, slots=True)
class _CapabilityProbe:
    work_id: str
    destination: str


class _ScanProgress(Protocol):
    """The scan snapshot shape a caller has already checked with ``getattr``."""

    updated_at: int


@dataclass(frozen=True, slots=True)
class _PendingManualReturn:
    context: tuple[str, str]
    hold_token: str
    scan_id: str
    requested_at: int


class ContinuousFieldEffectRepository(Protocol):
    def get_review_case_decisions(self, review_case_id: str) -> tuple[str, ...]: ...

    """Complete application-owned persistence contract for guarded work."""

    def pending_field_effect_plan_ids(self, limit: int) -> tuple[str, ...]: ...
    def pending_field_effect_action_plan_ids(self, limit: int) -> tuple[str, ...]: ...
    def field_effect_backup_ready(self, *, fresh: bool = False) -> bool: ...

    def field_effect_permission_readiness_snapshot(
        self, call: Callable[[bool | None], dict[str, object]]
    ) -> dict[str, object]: ...
    def field_effect_consent_snapshot(
        self, call: Callable[[], dict[str, object]]
    ) -> dict[str, object]: ...
    def pending_field_effect_decision_counts(self) -> tuple[dict[str, object], ...]: ...
    def grant_field_effect_consent_matrix(
        self,
        connection: str,
        grants: tuple[tuple[str, tuple[str, ...]], ...],
        granted_at: int,
    ) -> tuple[EffectConsent, ...]: ...
    def revoke_library_field_effect_consents(
        self, connection: str, library: str, changed_at: int
    ) -> int: ...
    def field_effect_manual_override(
        self, review_case_id: str, destination: str, changed_at: int
    ) -> int: ...
    def field_effect_manual_hold_context(
        self, review_case_id: str, destination: str
    ) -> tuple[str, str] | None: ...
    def field_effect_manual_hold_token(
        self, review_case_id: str, destination: str
    ) -> str | None: ...
    def request_field_effect_manual_return(
        self,
        review_case_id: str,
        destination: str,
        hold_token: str,
        scan_id: str,
        requested_at: int,
        changed_at: int,
    ) -> bool: ...
    def get_field_effect(self, plan_id: str) -> FieldEffectOperation: ...
    def pending_field_effect_decision_documents(
        self, limit: int, after: tuple[int, str] | None = None
    ) -> tuple[str, ...]: ...
    def pending_field_effect_activity_example_document(self) -> str | None: ...
    def record_current_field_effect_no_change(
        self, decision: AutomaticDecision, occurred_at: int
    ) -> bool: ...
    def retire_stale_cover_decision(
        self, decision_id: str, changed_at: int
    ) -> bool: ...
    def current_field_effect_consent(
        self, connection: str, library: str, destination: str
    ) -> EffectConsent: ...
    def suspend_field_effect_capability(
        self,
        *,
        scope: str,
        connection: str,
        destination: str,
        reason: str,
        changed_at: int,
    ) -> tuple[str, ...]: ...
    def clear_field_effect_capability_suspension(
        self,
        *,
        scope: str,
        connection: str,
        destination: str,
        reason: str,
        observed_at: int,
    ) -> None: ...
    def field_effect_capability_suspension(
        self, connection: str, destination: str
    ) -> str | None: ...
    def field_effect_connection_replan(
        self, decision_id: str
    ) -> FieldEffectOperation | None: ...
    def next_field_effect_connection_replan(
        self, connection: str, library: str, after: str | None
    ) -> tuple[str, str, str] | None: ...
    def next_field_effect_plan_id(
        self, last_library: str | None
    ) -> tuple[str, str] | None: ...
    def field_effect_activity_data(self, limit: int) -> dict[str, object]: ...
    def field_effect_activity_summary(self) -> dict[str, object]: ...

    def field_effect_activity_page(
        self,
        *,
        search: str | None,
        event_type: str,
        occurred_after: int,
        page: int,
        page_size: int,
    ) -> dict[str, object]: ...
    def field_effect_activity_transitions(
        self, event_id: str, after_sequence: int | None = None, limit: int = 20
    ) -> tuple[dict[str, object], ...]: ...
    def pending_field_effect_manual_returns(
        self,
        limit: int,
        after: tuple[int, str, str] | None = None,
    ) -> tuple[tuple[str, str, str, str, str, str, int, int], ...]: ...
    def clear_field_effect_manual_hold(
        self,
        review_case_id: str,
        destination: str,
        changed_at: int,
        expected_hold_token: str | None = None,
    ) -> tuple[str, str] | None: ...


def _waiting_reasons(
    *,
    counts: dict[str, int],
    prepared_by_destination: dict[tuple[str, str], int],
    capabilities: list[dict[str, object]],
    suspensions: list[dict[str, object]],
    provider_recovery: list[dict[str, int | str | list[str] | None]],
) -> dict[str, int]:
    """Split "waiting safely" into the reasons an owner can act on.

    One number for everything that is not moving cannot be acted on: an owner
    who reads `46 Waiting safely` cannot tell consent they can give from a retry
    they must wait out from a recovery they must repair. Each of these four is a
    different next step, and naming them is the whole of #639's second
    acceptance.

    Nothing here is a new source of truth. Every number is read off state the
    projection already assembled, which is why a reason can never disagree with
    the queue it describes.
    """
    consented_destinations = {
        (str(capability["library"]), str(destination["destination"]))
        for capability in capabilities
        for destination in cast("list[dict[str, object]]", capability["destinations"])
        if destination["consented"] is True
    }
    awaiting_permission = sum(
        count
        for key, count in prepared_by_destination.items()
        if key not in consented_destinations
    )
    return {
        # Prepared, and no consent covers the destination it would be written
        # to. The owner resolves this, and only the owner can.
        "awaiting_permission": awaiting_permission,
        # Consented and planned: the queue owns these and needs nothing.
        "scheduled": int(counts.get("planned", 0)),
        # Refused once for a reason a retry can change; time resolves it.
        "retrying": int(counts.get("retryable", 0)),
        # Held behind a suspended capability or a dormant provider connection:
        # a repair resolves it, not a decision and not time.
        "held_for_recovery": len(suspensions)
        + sum(
            int(cast(int, item["affected_count"]))
            for item in provider_recovery
            if item["state"] in {"waiting", "checking", "dormant", "exhausted"}
        ),
        # Moving right now, which is the one bucket that is not waiting at all.
        "applying": sum(
            int(counts.get(state, 0))
            for state in ("claimed", "applying", "reconciling")
        ),
    }


class ContinuousGuardedApplication:
    """Compose persisted decisions into one instance-scoped mutation slot."""

    # A class attribute rather than an instance one so a lane that was never
    # attached to a channel -- a test double, a composition without a browser --
    # still announces into the same shape instead of failing on a missing name.
    _announce: Callable[[str, str | None], object] = staticmethod(lambda *_args: None)

    def __init__(
        self,
        repository: ContinuousFieldEffectRepository,
        applications: dict[tuple[str, str], GuardedFieldApplication],
        capabilities: dict[tuple[str, str], CapabilityObserver],
        authority: AuthorityCurrentness,
        configured_libraries: tuple[tuple[str, str], ...],
        clock: Callable[[], int],
        request_scan: Callable[[str, str], object],
        *,
        polling_seconds: float = 2.0,
        operation_admission: OperationAdmission | None = None,
        graceful_shutdown_seconds: int = 30,
        scan_progress: Callable[[str, str], object] | None = None,
        activity_only: bool = False,
        provider_recovery_snapshot: Callable[
            [], tuple[dict[str, int | str | list[str] | None], ...]
        ]
        | None = None,
        activity_snapshot: Callable[
            [Callable[[], dict[str, object]]], dict[str, object]
        ]
        | None = None,
    ) -> None:
        if (
            (activity_only and (applications or capabilities))
            or (
                not activity_only
                and (
                    set(applications) != set(configured_libraries)
                    or set(capabilities) != set(configured_libraries)
                )
            )
            or not 0.1 <= polling_seconds <= 60.0
            or type(graceful_shutdown_seconds) is not int
            or not 1 <= graceful_shutdown_seconds <= 300
            or type(activity_only) is not bool
        ):
            raise GuardedApplicationError("configuration")
        connections = {connection for connection, _ in configured_libraries}
        if len(connections) != 1:
            raise GuardedApplicationError("configuration")
        self._operation_admission = operation_admission
        self._repository = repository
        self._applications = dict(applications)
        self._capabilities = dict(capabilities)
        self._authority = authority
        self._libraries = configured_libraries
        self._clock = clock
        self._request_scan = request_scan
        self._polling_seconds = polling_seconds
        self._graceful_shutdown_seconds = graceful_shutdown_seconds
        self._scan_progress = scan_progress
        self._activity_only = activity_only
        self._provider_recovery_snapshot = provider_recovery_snapshot or (lambda: ())
        self._activity_snapshot = activity_snapshot or (lambda call: call())
        self._activity_reader_lock = threading.Lock()
        self._slot = threading.Lock()
        self._admission = threading.Event()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dormant_start = False
        self._last_library: str | None = None
        self._last_failure: str | None = None
        self._failed_decision: tuple[str, str] | None = None
        self._schedule_cursor: tuple[int, str] | None = None
        self._schedule_generation = 0
        self._schedule_lock = threading.Lock()
        # Libraries whose released decisions wait for a lost connection, and
        # the plan each one last probed it through (#1402).
        self._replans_waiting: set[tuple[str, str]] = set()
        self._replan_probe_after: dict[tuple[str, str], str] = {}
        self._manual_return_cursor: tuple[int, str, str] | None = None
        self._capability_cache: dict[str, tuple[int, dict[str, object]]] = {}
        self._capability_lock = threading.Lock()
        self._serialize: Callable[[Callable[[], object]], object] = lambda call: call()

    def attach_serialization(
        self, serialize: Callable[[Callable[[], object]], object]
    ) -> None:
        self._serialize = serialize

    def attach_updates(self, announce: Callable[[str, str | None], object]) -> None:
        """Bind the invalidation channel this lane announces write state on.

        The lane advances a plan every couple of seconds on its own thread with
        no request in flight, so without this the only way an owner learned that
        a prepared change had started applying was the next poll. What is
        announced is the topic and nothing else: the queue's own state stays
        where it is durable, and the surface reads it back over HTTP (#639).
        """
        self._announce = announce

    def attach_timing(self, timing: StageTimer) -> None:
        for application in self._applications.values():
            application.attach_timing(timing)

    def start(self) -> tuple[int, int, int]:
        self._stop.clear()
        self._admission.set()
        for application in self._applications.values():
            application.resume_admission()
        recovered, actions, cleanups = self._recover_startup()
        self._thread = threading.Thread(
            target=self._run, name="guarded-field-application", daemon=True
        )
        self._thread.start()
        self._wake.set()
        return recovered, actions, cleanups

    def start_dormant(self) -> None:
        """Start the worker behind a closed latch without claiming work."""
        if self._thread is not None:
            raise GuardedApplicationError("configuration")
        self._stop.clear()
        self._admission.clear()
        self._dormant_start = True
        for application in self._applications.values():
            application.begin_drain()
        self._thread = threading.Thread(
            target=self._run, name="guarded-field-application", daemon=True
        )
        self._thread.start()

    def admit(self) -> None:
        """Open the already-started worker and target admission latches."""
        for application in self._applications.values():
            application.resume_admission()
        self._admission.set()
        self._wake.set()

    def suspend_admission(self) -> None:
        """Reject new claims without stopping the existing worker."""
        for application in self._applications.values():
            application.begin_drain()

    def resume_admission(self) -> None:
        for application in self._applications.values():
            application.resume_admission()
        self._wake.set()

    def _recover_startup(self) -> tuple[int, int, int]:
        cleanups = 0
        failures: list[Exception] = []
        recovered = self._drain_recovery(
            self._repository.pending_field_effect_plan_ids, self.apply, failures
        )
        actions = self._drain_recovery(
            self._repository.pending_field_effect_action_plan_ids,
            self.reconcile,
            failures,
        )
        first = next(iter(self._applications.values()))
        while True:
            try:
                completed = first.run_pending_cleanups(limit=100)
            except Exception as error:
                failures.append(error)
                break
            cleanups += completed
            if completed < 100:
                break
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup("startup recovery failures", failures)
        return recovered, actions, cleanups

    def _drain_recovery(
        self,
        pending: Callable[[int], tuple[str, ...]],
        recover: Callable[[str], object],
        failures: list[Exception],
    ) -> int:
        """Drain one paged restart obligation without one plan stopping it.

        Every plan on a page is attempted; a plan that fails is collected and
        withheld from later pages, because it stays pending and would otherwise
        return forever. That is why the non-progress guard reads the withheld
        set rather than the pending page: it has to mean "nothing advanced",
        not "a plan was seen twice", or a plan the batch has already given up
        on would abort recovery for the plans still moving. The returned count
        is exactly what advanced; nothing that failed becomes invisible.

        The paging seam takes no cursor, so a whole page of withheld plans
        still hides the plans behind it until one of them is repaired. Each of
        those plans has already reported its own failure by then, which is the
        bound this restart owes: loud for what failed, never silent about it.
        """
        recovered = 0
        attempted: set[str] = set()
        withheld: set[str] = set()
        while True:
            try:
                page = pending(100)
            except Exception as error:
                failures.append(error)
                return recovered
            remaining = tuple(plan_id for plan_id in page if plan_id not in withheld)
            if not remaining:
                return recovered
            if any(plan_id in attempted for plan_id in remaining):
                failures.append(GuardedApplicationError("recovery"))
                return recovered
            for plan_id in remaining:
                attempted.add(plan_id)
                try:
                    recover(plan_id)
                except Exception as error:
                    failures.append(error)
                    withheld.add(plan_id)
                    continue
                recovered += 1

    def close_admission(self) -> None:
        """Stop new claims without waiting for the active worker."""
        self._admission.set()
        self._stop.set()
        self._wake.set()
        for application in self._applications.values():
            application.begin_drain()

    def wait_for_drain(self, deadline: float | None = None) -> None:
        """Wait for the active worker within one caller-owned deadline."""
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            timeout = (
                self._graceful_shutdown_seconds
                if deadline is None
                else max(deadline - time.monotonic(), 0.0)
            )
            thread.join(timeout=timeout)
        if thread is not None and thread.is_alive():
            self._thread = thread
            raise GuardedApplicationError("shutdown_timeout")
        self._thread = None

    def begin_drain(self, deadline: float | None = None) -> None:
        self.close_admission()
        self.wait_for_drain(deadline)

    def wake(self) -> None:
        self._restart_schedule()
        self._wake.set()

    def _restart_schedule(self) -> None:
        with self._schedule_lock:
            self._schedule_generation += 1
            self._schedule_cursor = None

    def _wait_for_connection(self, key: tuple[str, str]) -> None:
        with self._schedule_lock:
            self._replans_waiting.add(key)

    def _observe_waiting_connections(self) -> None:
        """Notice, once a pass, that a lost connection is healthy again (#1402).

        Nothing else revisits a decision a lost connection released: its plan
        is no longer ready, and the schedule cursor is already past it. Each
        library waiting on its connection is observed through one of its
        waiting plans, rotating so that one item that keeps failing cannot hide
        a recovery, and the schedule starts over once nothing is suspended.
        """
        with self._schedule_lock:
            waiting = sorted(self._replans_waiting)
        affected_review_case_ids: set[str] = set()
        try:
            for key in waiting:
                connection, library = key
                suspension = self._repository.field_effect_capability_suspension(
                    connection, ""
                )
                if suspension == "connection_unavailable":
                    probe = self._repository.next_field_effect_connection_replan(
                        connection, library, self._replan_probe_after.get(key)
                    )
                    capability = self._capabilities.get(key)
                    if probe is None or capability is None:
                        suspension = "nothing_waiting"
                    else:
                        plan_id, work_id, destination = probe
                        self._replan_probe_after[key] = plan_id
                        observed_at = self._clock()
                        observed = capability.observe(
                            _CapabilityProbe(work_id, destination)  # type: ignore[arg-type]  # reason: observers read only work_id and destination, which the probe supplies.
                        )
                        if not self._schedule_capability_ready(
                            observed.category,
                            connection,
                            destination,
                            observed_at,
                            affected_review_case_ids,
                        ):
                            continue
                        suspension = None
                with self._schedule_lock:
                    self._replans_waiting.discard(key)
                if suspension is None:
                    self._restart_schedule()
        finally:
            self._flush_affected_review_case_ids(affected_review_case_ids)

    def _advance_schedule_cursor(
        self,
        decision: AutomaticDecision
        | CoverAutomaticDecision
        | HumanDecision
        | AuthoredDescriptionDecision,
        generation: int,
    ) -> None:
        with self._schedule_lock:
            if generation == self._schedule_generation:
                self._schedule_cursor = (
                    decision.occurred_at,
                    decision.decision_id,
                )

    def enable(
        self, grants: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> dict[str, object]:
        configured = tuple(library for _, library in self._libraries)
        if (
            type(grants) is not tuple
            or not grants
            or any(type(grant) is not tuple or len(grant) != 2 for grant in grants)
        ):
            raise GuardedApplicationError("consent")
        libraries = tuple(grant[0] for grant in grants)
        submitted = {library: destinations for library, destinations in grants}
        if len(set(libraries)) != len(libraries) or any(
            type(library) is not str
            or library not in configured
            or type(destinations) is not tuple
            or not destinations
            or len(set(destinations)) != len(destinations)
            or any(
                type(destination) is not str
                or destination not in SUPPORTED_DESTINATIONS
                for destination in destinations
            )
            for library, destinations in grants
        ):
            raise GuardedApplicationError("consent")
        if not self._repository.field_effect_backup_ready(fresh=True):
            raise GuardedApplicationError("backup")
        prepared = self._repository.pending_field_effect_decision_counts()
        prepared_libraries = {
            str(item["library"])
            for item in prepared
            if int(cast(int, item["count"])) > 0
        }
        if any(library not in prepared_libraries for library in libraries):
            raise GuardedApplicationError("not_ready")
        readiness = self._capability_readiness(libraries, force=True)
        eligible = {
            str(item["library"]): tuple(
                str(destination["destination"])
                for destination in cast("list[dict[str, object]]", item["destinations"])
                if destination["state"] == "allowed"
            )
            for item in readiness
        }
        if any(item["state"] == "connection_unavailable" for item in readiness) or any(
            not eligible[str(item["library"])] for item in readiness
        ):
            raise GuardedApplicationError("capability")
        if any(
            set(submitted[library]) != set(eligible[library]) for library in libraries
        ):
            raise GuardedApplicationError("capability")
        connection = self._libraries[0][0]
        self._repository.grant_field_effect_consent_matrix(
            connection,
            tuple((library, tuple(sorted(eligible[library]))) for library in libraries),
            self._clock(),
        )
        self.wake()
        return self.activity()

    def destinations_consented(
        self, connection: str, library: str, destinations: tuple[str, ...]
    ) -> bool:
        """Answer whether a decision on any of these destinations would apply.

        This reads the same consent the applier reads at
        `current_field_effect_consent`; it grants nothing and changes nothing.
        A review surface needs it so its reassurance copy can say what is
        actually true of a recorded decision instead of assuming one branch.
        """
        if (connection, library) not in self._applications:
            return False
        for destination in destinations:
            try:
                self._repository.current_field_effect_consent(
                    connection, library, destination
                )
            except FieldEffectError as error:
                if error.code == "consent":
                    continue
                return False
            return True
        return False

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
        self._announce("activity", None)
        if not paused:
            self.wake()

    def activate_human_override(
        self,
        review_case_id: str,
        destination: str,
    ) -> None:
        self._repository.field_effect_manual_override(
            review_case_id, destination, self._clock()
        )
        self._announce("activity", None)
        self.wake()

    def return_to_automatic(
        self,
        review_case_id: str,
        destination: str,
    ) -> None:
        context = self._repository.field_effect_manual_hold_context(
            review_case_id, destination
        )
        if context is None:
            raise GuardedApplicationError("manual_hold")
        hold_token = self._repository.field_effect_manual_hold_token(
            review_case_id, destination
        )
        if hold_token is None:
            raise GuardedApplicationError("manual_hold")
        try:
            progress = self._request_scan(*context)
        except Exception as error:
            # ``_request_scan`` is an injected callable reaching the target
            # through the preparation pipeline, so its raisable set belongs to
            # whatever the composition root bound. A scan that was not
            # requested cannot release the hold, and breadth is what keeps this
            # fail-closed: the hold stays until a scan is demonstrably running.
            raise GuardedApplicationError("scan") from error
        scan_id = getattr(progress, "scan_id", None)
        requested_at = getattr(progress, "updated_at", None)
        if type(scan_id) is not str or type(requested_at) is not int:
            raise GuardedApplicationError("scan")
        persisted = self._repository.request_field_effect_manual_return(
            review_case_id,
            destination,
            hold_token,
            scan_id,
            requested_at,
            self._clock(),
        )
        if not persisted:
            raise GuardedApplicationError("manual_hold")
        self._wake.set()

    def status(self, plan_id: str) -> FieldEffectOperation:
        return self._repository.get_field_effect(plan_id)

    def apply(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        application = self._application(operation)
        affected_review_case_ids: set[str] = set()
        completed = False
        delegated_error: BaseException | None = None
        try:
            with self._slot:
                applied = application.apply(plan_id, affected_review_case_ids)
            completed = True
            if (applied.state, applied.failure) == ("stale", "connection"):
                self._wait_for_connection(
                    (applied.plan.connection, applied.plan.library)
                )
            return applied
        except BaseException as error:
            delegated_error = error
            raise
        finally:
            try:
                self._flush_application_updates(affected_review_case_ids, completed)
            except BaseException:
                if delegated_error is None:
                    raise

    def reconcile(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        application = self._application(operation)
        affected_review_case_ids: set[str] = set()
        completed = False
        delegated_error: BaseException | None = None
        try:
            with self._slot:
                reconciled = application.reconcile(plan_id, affected_review_case_ids)
            completed = True
            return reconciled
        except BaseException as error:
            delegated_error = error
            raise
        finally:
            try:
                self._flush_application_updates(affected_review_case_ids, completed)
            except BaseException:
                if delegated_error is None:
                    raise

    def _flush_application_updates(
        self, affected_review_case_ids: set[str], completed: bool
    ) -> None:
        if not completed and not affected_review_case_ids:
            return
        self._announce("activity", None)
        for review_case_id in sorted(affected_review_case_ids):
            self._announce("library", review_case_id)

    def _flush_affected_review_case_ids(
        self, affected_review_case_ids: set[str]
    ) -> None:
        if not affected_review_case_ids:
            return
        self._announce("activity", None)
        for review_case_id in sorted(affected_review_case_ids):
            self._announce("library", review_case_id)

    def schedule(self, limit: int = 100) -> int:
        affected_review_case_ids: set[str] = set()
        try:
            return self._schedule(limit, affected_review_case_ids)
        finally:
            self._flush_affected_review_case_ids(affected_review_case_ids)

    def _set_last_failure(self, code: str | None) -> None:
        if code != self._last_failure:
            self._last_failure = code
            self._announce("activity", None)

    def _clear_decision_failure(self, decision_id: str) -> None:
        if (
            self._failed_decision is not None
            and self._failed_decision[0] == decision_id
        ):
            self._failed_decision = None
            self._set_last_failure(None)

    def _schedule_capability_ready(
        self,
        category: str,
        connection: str,
        destination: str,
        observed_at: int,
        affected_review_case_ids: set[str],
    ) -> bool:
        """Apply the same recoverable suspension rule to each planned member."""
        if category != "allowed":
            scope = (
                "connection" if category == "connection_unavailable" else "destination"
            )
            affected_review_case_ids.update(
                self._repository.suspend_field_effect_capability(
                    scope=scope,
                    connection=connection,
                    destination=destination,
                    reason=category,
                    changed_at=observed_at,
                )
            )
            return False
        for reason in ("insufficient_permission", "field_unavailable"):
            self._repository.clear_field_effect_capability_suspension(
                scope="destination",
                connection=connection,
                destination=destination,
                reason=reason,
                observed_at=observed_at,
            )
        self._repository.clear_field_effect_capability_suspension(
            scope="connection",
            connection=connection,
            destination="",
            reason="connection_unavailable",
            observed_at=observed_at,
        )
        return (
            self._repository.field_effect_capability_suspension(connection, destination)
            is None
        )

    def _schedule(self, limit: int, affected_review_case_ids: set[str]) -> int:
        self._release_completed_manual_returns()
        with self._schedule_lock:
            generation = self._schedule_generation
            cursor = self._schedule_cursor
        documents = self._repository.pending_field_effect_decision_documents(
            limit, cursor
        )
        planned = 0
        for document in documents:
            decision = self._decision(document)
            if (
                type(decision) is HumanDecision
                and decision.application == "recorded_only"
            ):
                # A decision retained from before audiobook writes existed
                # is history, not pending work. Advancing past it keeps the
                # schedule moving and forms no plan the target could ever
                # receive (#968).
                self._advance_schedule_cursor(decision, generation)
                continue
            if (
                type(decision) is AutomaticDecision
                or type(decision) is CoverAutomaticDecision
                and decision.qualification_identity is not None
            ) and not qualification_manifest_is_current(
                decision.policy_category,
                decision.policy_option,
                decision.qualification_manifest_sha256,
            ):
                self._advance_schedule_cursor(decision, generation)
                continue
            # This is intentionally before authority, consent and capability: an
            # equal automatic candidate is a terminal decision fact, not a write.
            no_change = getattr(
                self._repository, "record_current_field_effect_no_change", None
            )
            try:
                if (
                    type(decision) in (AutomaticDecision, AuthoredDescriptionDecision)
                    and no_change is not None
                    and no_change(decision, self._clock())
                ):
                    self._clear_decision_failure(decision.decision_id)
                    self._advance_schedule_cursor(decision, generation)
                    continue
                snapshot = self._authority.resolve(decision)
            except (FieldEffectError, GuardedApplicationError, ValueError) as error:
                # Retirement answers a binding that no longer holds. A storage
                # fault says nothing about the selection, and retiring on one
                # would write a `stale` cover event and swallow the fault, so
                # it propagates instead. The resolver only began naming this
                # code when its storage faults stopped escaping untyped.
                code = getattr(error, "code", None)
                if (
                    code != "persistence"
                    and decision.destination == "cover"
                    and self._repository.retire_stale_cover_decision(
                        decision.decision_id, self._clock()
                    )
                ):
                    self._advance_schedule_cursor(decision, generation)
                    continue
                # One decision that cannot bind is a fact about that decision,
                # not about the queue behind it. The pending order is a single
                # union across every library, so aborting here starves every
                # later decision -- human accepts included -- until a restart
                # meets the same row again. Name the failure and move on; a
                # storage fault still propagates, because it says nothing
                # about this decision and the next read would fault too.
                if (
                    code != "persistence"
                    and decision.destination != "cover"
                    and type(error) is FieldEffectError
                ):
                    self._failed_decision = (
                        decision.decision_id,
                        code or "unavailable",
                    )
                    self._set_last_failure(code or "unavailable")
                    self._advance_schedule_cursor(decision, generation)
                    continue
                raise
            self._clear_decision_failure(decision.decision_id)
            key = (snapshot.connection, snapshot.library)
            application = self._applications.get(key)
            capability = self._capabilities.get(key)
            if application is None or capability is None:
                self._advance_schedule_cursor(decision, generation)
                continue
            try:
                consent = self._repository.current_field_effect_consent(
                    snapshot.connection, snapshot.library, snapshot.destination
                )
            except FieldEffectError as error:
                if error.code == "consent":
                    self._advance_schedule_cursor(decision, generation)
                    continue
                raise
            del consent
            # A decision released by a lost connection is planned again with
            # its waiting plan's capability while nothing is suspended: the
            # preclaim's fresh observation then decides, and counts a refusal
            # against the bound. Under a suspension it is observed here, as
            # any other decision is (#1402).
            released = self._repository.field_effect_connection_replan(
                decision.decision_id
            )
            observed_at = self._clock()
            if (
                released is not None
                and self._repository.field_effect_capability_suspension(
                    snapshot.connection, snapshot.destination
                )
                is None
            ):
                capability_digest = released.plan.capability_digest
            else:
                observed = capability.observe(
                    _CapabilityProbe(snapshot.work_id, snapshot.destination)  # type: ignore[arg-type]  # reason: observers read only work_id and destination, which the probe supplies; a full FieldEffectPlan does not exist before planning.
                )
                if not self._schedule_capability_ready(
                    observed.category,
                    snapshot.connection,
                    snapshot.destination,
                    observed_at,
                    affected_review_case_ids,
                ):
                    if (
                        released is not None
                        and observed.category == "connection_unavailable"
                    ):
                        self._wait_for_connection(key)
                    self._advance_schedule_cursor(decision, generation)
                    continue
                capability_digest = observed.digest
            operation: FieldEffectOperation | None
            if snapshot.paired_bundle_id is not None:
                if (
                    decision.destination != "title"
                    or type(decision) is not AutomaticDecision
                ):
                    self._advance_schedule_cursor(decision, generation)
                    continue
                siblings = tuple(
                    self._decision(raw)
                    for raw in self._repository.get_review_case_decisions(
                        decision.review_case_id
                    )
                )
                sibling = next(
                    (
                        item
                        for item in siblings
                        if item.decision_id == snapshot.paired_decision_id
                    ),
                    None,
                )
                if (
                    type(sibling) is not AutomaticDecision
                    or sibling.destination != "subtitle"
                ):
                    self._advance_schedule_cursor(decision, generation)
                    continue
                sibling_snapshot = self._authority.resolve(sibling)
                if (
                    sibling_snapshot.paired_bundle_id != snapshot.paired_bundle_id
                    or sibling_snapshot.paired_decision_id != decision.decision_id
                ):
                    self._advance_schedule_cursor(decision, generation)
                    continue
                try:
                    self._repository.current_field_effect_consent(
                        snapshot.connection, snapshot.library, "subtitle"
                    )
                except FieldEffectError as error:
                    if error.code != "consent":
                        raise
                    self._advance_schedule_cursor(decision, generation)
                    continue
                waiting = None if released is None else released.plan.subtitle_companion
                if (
                    waiting is not None
                    and self._repository.field_effect_capability_suspension(
                        snapshot.connection, "subtitle"
                    )
                    is None
                ):
                    companion_digest = waiting.capability_digest
                else:
                    companion_capability = capability.observe(
                        _CapabilityProbe(snapshot.work_id, "subtitle")  # type: ignore[arg-type]  # reason: observers read work_id and destination; the paired plan does not exist until both capabilities pass.
                    )
                    if not self._schedule_capability_ready(
                        companion_capability.category,
                        snapshot.connection,
                        "subtitle",
                        observed_at,
                        affected_review_case_ids,
                    ):
                        if (
                            released is not None
                            and companion_capability.category
                            == "connection_unavailable"
                        ):
                            self._wait_for_connection(key)
                        self._advance_schedule_cursor(decision, generation)
                        continue
                    companion_digest = companion_capability.digest
                operation = application.plan_title_subtitle(
                    decision,
                    sibling,
                    title_capability_digest=capability_digest,
                    subtitle_capability_digest=companion_digest,
                )
            else:
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
                        snapshot.dependency_digests or (snapshot.target_fingerprint,)
                    ),
                    capability_digest=capability_digest,
                    allow_corrective_keep_current=(
                        type(decision) is HumanDecision
                        and decision.outcome == "keep_current"
                    ),
                )
            if operation is not None:
                planned += 1
            self._advance_schedule_cursor(decision, generation)
        if planned and not affected_review_case_ids:
            self._announce("activity", None)
        return planned

    def run_cycle(self) -> bool:
        """Report bounded selection progress or an applied plan, not plan count."""
        # Each repository operation owns its local database boundary. Selection
        # stays bounded while target observation and application run outside it.
        with self._schedule_lock:
            before = (self._schedule_generation, self._schedule_cursor)
        self.schedule(1)
        selected = cast(
            "tuple[str, str] | None",
            self._serialize(
                lambda: self._repository.next_field_effect_plan_id(self._last_library)
            ),
        )
        if selected is None:
            with self._schedule_lock:
                return before != (self._schedule_generation, self._schedule_cursor)
        plan_id, library = selected
        self.apply(plan_id)
        self._last_library = library
        return True

    def classify_request(self, work_class: WorkClass) -> AbstractContextManager[None]:
        """Carry purpose to this reader without entering its admission queue."""
        return (
            nullcontext()
            if self._operation_admission is None
            else self._operation_admission.classify(work_class)
        )

    def activity(
        self,
        *,
        search: str | None = None,
        event_type: str = "all",
        range_days: int = 30,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
        normalized_search = search.strip().casefold() if search is not None else None
        if (
            (
                normalized_search is not None
                and (not normalized_search or len(normalized_search) > 128)
            )
            or event_type
            not in {"all", "review", "policy", "metadata", "safety", "unknown"}
            or range_days not in {7, 30, 90}
            or type(page) is not int
            or not 1 <= page <= 1_000_000
            or page_size not in {25, 50, 100}
        ):
            raise GuardedApplicationError("activity_query")
        with self._activity_reader_lock:
            return self._activity_snapshot(
                lambda: self._activity_projection(
                    normalized_search=normalized_search,
                    event_type=event_type,
                    range_days=range_days,
                    page=page,
                    page_size=page_size,
                )
            )

    def activity_consent(self) -> dict[str, object]:
        """Read the consent inputs Activity states, without its history (#1236).

        Library's write-ready notice needs only these, and none is an Activity
        fact, so the read takes no catch-up and waits for no history step.
        """
        with self._activity_reader_lock:
            return self._repository.field_effect_consent_snapshot(self._consent_state)

    def _consent_state(self) -> dict[str, object]:
        permission, waiting, _ = self._consent_projection(
            self._repository.field_effect_activity_summary()
        )
        return {
            "backup_ready": None,
            "capabilities": permission["capabilities"],
            "configured_libraries": permission["configured_libraries"],
            "lapsed_consents": permission["lapsed_consents"],
            "waiting": waiting,
        }

    def activity_permission_readiness(self) -> dict[str, object]:
        with self._activity_reader_lock:
            return self._repository.field_effect_permission_readiness_snapshot(
                self._permission_readiness_projection
            )

    def _permission_readiness_projection(
        self, backup_ready: bool | None
    ) -> dict[str, object]:
        permission, _ = self._permission_projection(
            self._repository.field_effect_activity_summary()
        )
        permission.pop("enabled_libraries")
        permission.pop("lapsed_consents")
        example_document = (
            self._repository.pending_field_effect_activity_example_document()
        )
        example: dict[str, object] | None = None
        if example_document is not None:
            try:
                snapshot = self._authority.resolve(self._decision(example_document))
                example = {
                    "after": project_decision_value(
                        snapshot.destination, snapshot.decision_value
                    ),
                    "before": snapshot.pre_value,
                    "destination": snapshot.destination,
                    "library": snapshot.library,
                    "work_id": snapshot.work_id,
                }
            except (FieldEffectError, GuardedApplicationError, ValueError) as error:
                # A binding race removes the optional illustration. A storage
                # fault cannot be represented as an absent prepared example.
                if getattr(error, "code", None) == "persistence":
                    raise
        return {**permission, "backup_ready": backup_ready, "example": example}

    def _permission_projection(
        self, data: dict[str, object]
    ) -> tuple[dict[str, object], dict[tuple[str, str], int]]:
        """Share the exact destination and prepared-work gates across both reads."""
        consented = {
            (
                str(value["connection"]),
                str(value["library"]),
                str(value["destination"]),
            )
            for value in cast(
                "list[dict[str, object]]",
                data.pop("consented_destinations", []),
            )
        }
        prepared_rows = self._repository.pending_field_effect_decision_counts()
        prepared_by_library: dict[str, dict[str, int]] = {
            library: {"automatic": 0, "human": 0, "total": 0}
            for _, library in self._libraries
        }
        # Prepared work that no destination in the offer can carry is what an
        # owner is actually looking at when a screen says a number and an
        # `Enable writes` action beside it. Keeping the two apart is why this is
        # counted per destination rather than per library (#639).
        prepared_by_destination: dict[tuple[str, str], int] = {}
        for item in prepared_rows:
            library = str(item["library"])
            origin = str(item["origin"])
            prepared_destination = str(item["destination"])
            count = int(cast(int, item["count"]))
            values = prepared_by_library.setdefault(
                library, {"automatic": 0, "human": 0, "total": 0}
            )
            values[origin] += count
            values["total"] += count
            key = (library, prepared_destination)
            prepared_by_destination[key] = prepared_by_destination.get(key, 0) + count
        enabled = []
        for connection, library in self._libraries:
            if any(
                candidate_connection == connection and candidate_library == library
                for candidate_connection, candidate_library, _ in consented
            ):
                enabled.append({"connection": connection, "library": library})
        capabilities = self._capability_readiness(
            tuple(library for _, library in self._libraries), force=False
        )
        for capability in capabilities:
            connection = str(capability["connection"])
            library = str(capability["library"])
            for destination in cast(
                "list[dict[str, object]]", capability["destinations"]
            ):
                name = str(destination["destination"])
                destination["consented"] = (connection, library, name) in consented
                destination["prepared"] = prepared_by_destination.get(
                    (library, name), 0
                )
        prepared = {
            "automatic": sum(
                value["automatic"] for value in prepared_by_library.values()
            ),
            "human": sum(value["human"] for value in prepared_by_library.values()),
            "total": sum(value["total"] for value in prepared_by_library.values()),
        }
        return {
            "configured_libraries": [
                {
                    "connection": connection,
                    "library": library,
                    "prepared": prepared_by_library[library],
                }
                for connection, library in self._libraries
            ],
            "capabilities": capabilities,
            "enabled_libraries": enabled,
            # A lapse is stated only for a library this instance still
            # configures, beside the offer that could restore it (#1370).
            "lapsed_consents": [
                value
                for value in cast(
                    "list[dict[str, object]]", data.pop("lapsed_consents")
                )
                if (value["connection"], value["library"]) in self._libraries
            ],
            "prepared": prepared,
        }, prepared_by_destination

    def _activity_projection(
        self,
        *,
        normalized_search: str | None,
        event_type: str,
        range_days: int,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        """Project Activity inside the repository's exact read boundary."""

        data = self._repository.field_effect_activity_summary()
        page_projection = self._repository.field_effect_activity_page(
            search=normalized_search,
            event_type=event_type,
            occurred_after=max(0, self._clock() - range_days * 86_400),
            page=page,
            page_size=page_size,
        )
        data["events"] = page_projection["events"]
        data["report_documents"] = page_projection["report_documents"]
        data["current_waiting"] = page_projection["current_waiting"]
        events = data.get("events", [])
        report_documents = data.pop("report_documents", {})
        if (
            type(events) is not list
            or type(report_documents) is not dict
            # A 100-row Activity page may legitimately reference 100 distinct
            # reports.  Keep the application boundary aligned with that closed
            # page-size limit rather than rejecting a valid paged projection.
            or len(report_documents) > 100
            or any(
                type(key) is not str or type(value) is not str
                for key, value in report_documents.items()
            )
        ):
            raise GuardedApplicationError("activity")
        validated_reports = page_projection.get("validated_reports")
        if "validated_reports" in page_projection:
            try:
                if (
                    type(validated_reports) is not dict
                    or set(validated_reports) != set(report_documents)
                    or any(
                        type(key) is not str
                        or type(value) is not MetadataReport
                        or not report_documents[key]
                        or len(report_documents[key].encode("utf-8")) > MAX_REPORT_BYTES
                        or value.report_id != key
                        or value.to_json() != report_documents[key]
                        for key, value in validated_reports.items()
                    )
                ):
                    raise GuardedApplicationError("activity")
            except (ReportValueError, UnicodeError) as error:
                raise GuardedApplicationError("activity") from error
        reports: dict[str, MetadataReport] = {}
        current_waiting = data.get("current_waiting")
        if current_waiting is not None and (
            type(current_waiting) is not dict
            or set(current_waiting)
            != {"destination", "library", "state", "work_id", "work_label"}
            or any(
                type(value) is not str or len(value) > 512
                for value in current_waiting.values()
            )
            or current_waiting["state"]
            not in {"planned", "claimed", "applying", "reconciling", "retryable"}
        ):
            raise GuardedApplicationError("activity")
        for index, event in enumerate(events):
            if type(event) is ReadableActivityEvent:
                events[index] = event.to_data()
                continue
            if type(event) is not dict:
                raise GuardedApplicationError("activity")
            connection = event.get("connection")
            report_id = event.get("report_id")
            if connection is None and report_id is None:
                project_readable_activity_event(event, None)
                continue
            if type(connection) is not str or type(report_id) is not str:
                raise GuardedApplicationError("activity")
            report = reports.get(report_id)
            if report is None:
                report_document = report_documents.get(report_id)
                if type(report_document) is not str:
                    raise GuardedApplicationError("activity")
                try:
                    report = (
                        cast("dict[str, MetadataReport]", validated_reports)[report_id]
                        if validated_reports is not None
                        else MetadataReport.from_json(report_document)
                    )
                except ReportValueError as error:
                    # ``from_json`` now funnels every malformed-document
                    # outcome, the length guard's codec failure included, into
                    # its one declared type, so an unreadable row is one
                    # ``activity`` refusal and a fault in the parser itself is
                    # no longer reclassified as one.
                    raise GuardedApplicationError("activity") from error
                if report.report_id != report_id:
                    raise GuardedApplicationError("activity")
                reports[report_id] = report
            try:
                project_readable_activity_event(event, report)
            except ActivityProjectionError as error:
                raise GuardedApplicationError("activity") from error
        cleanup = data.pop("cleanup", None)
        if cleanup is not None and (
            type(cleanup) is not dict
            or set(cleanup) != {"pending", "exhausted"}
            or any(type(value) is not int or value < 0 for value in cleanup.values())
        ):
            raise GuardedApplicationError("activity")
        permission, waiting, provider_recovery = self._consent_projection(data)
        return {
            **data,
            **permission,
            "backup_ready": None,
            "waiting": waiting,
            "example": None,
            "last_failure": self._last_failure,
            "ledger": {
                "event_type": event_type,
                "page": int(cast(int, page_projection["page"])),
                "page_count": int(cast(int, page_projection["page_count"])),
                "page_size": int(cast(int, page_projection["page_size"])),
                "query": normalized_search,
                "range_days": range_days,
                "total": int(cast(int, page_projection["total"])),
            },
            "schema_version": "continuous-field-activity-v3",
            "provider_recovery": provider_recovery,
        }

    def _consent_projection(
        self, data: dict[str, object]
    ) -> tuple[
        dict[str, object],
        dict[str, int],
        list[dict[str, int | str | list[str] | None]],
    ]:
        """Project the gates Activity's consent section reads, for both reads.

        Activity and the narrow consent read call this one projection, so the
        Library notice and Activity cannot come to state different offers.
        """
        permission, prepared_by_destination = self._permission_projection(data)
        provider_recovery = list(self._provider_recovery_snapshot())
        if len(provider_recovery) > 100 or any(
            type(item) is not dict
            or set(item)
            not in (
                {"state", "next_eligible_at", "affected_count"},
                {"state", "next_eligible_at", "affected_count", "dispatch_reasons"},
                {"state", "next_eligible_at", "affected_count", "failure_reasons"},
                {
                    "state",
                    "next_eligible_at",
                    "affected_count",
                    "dispatch_reasons",
                    "failure_reasons",
                },
            )
            or item["state"]
            not in {
                "waiting",
                "checking",
                "dormant",
                "recovered",
                "fanout",
                "exhausted",
            }
            or type(item["affected_count"]) is not int
            or not 1 <= item["affected_count"] <= 1_000_000
            or (
                "failure_reasons" in item
                and (
                    type(item["failure_reasons"]) is not list
                    or len(item["failure_reasons"]) > 7
                    or any(
                        type(reason) is not str or reason not in RECOVERY_FAILURES
                        for reason in item["failure_reasons"]
                    )
                    or item["failure_reasons"] != sorted(set(item["failure_reasons"]))
                )
            )
            or (
                "dispatch_reasons" in item
                and (
                    type(item["dispatch_reasons"]) is not list
                    or len(item["dispatch_reasons"])
                    != len(set(item["dispatch_reasons"]))
                    or any(
                        reason
                        not in {
                            "missing_corroboration",
                            "zero_provider_evidence",
                            "settleable_disagreement",
                            "blanket_scan",
                        }
                        for reason in item["dispatch_reasons"]
                    )
                )
            )
            or (
                item["next_eligible_at"] is not None
                and (
                    type(item["next_eligible_at"]) is not int
                    or not 0 <= item["next_eligible_at"] <= 8_640_000_000_000
                )
            )
            for item in provider_recovery
        ):
            raise GuardedApplicationError("activity")
        waiting = _waiting_reasons(
            counts=cast("dict[str, int]", data.get("counts", {})),
            prepared_by_destination=prepared_by_destination,
            capabilities=cast("list[dict[str, object]]", permission["capabilities"]),
            suspensions=cast("list[dict[str, object]]", data.get("suspensions", [])),
            provider_recovery=provider_recovery,
        )
        return permission, waiting, provider_recovery

    def activity_transitions(
        self, event_id: str, after_sequence: int | None = None
    ) -> dict[str, object]:
        with self._activity_reader_lock:
            transitions = self._repository.field_effect_activity_transitions(
                event_id, after_sequence
            )
        if len(transitions) > 21:
            raise GuardedApplicationError("activity")
        return {"events": list(transitions[:20]), "has_more": len(transitions) > 20}

    def _capability_readiness(
        self,
        libraries: tuple[str, ...],
        *,
        force: bool,
    ) -> list[dict[str, object]]:
        affected_review_case_ids: set[str] = set()
        try:
            return self._collect_capability_readiness(
                libraries,
                force=force,
                affected_review_case_ids=affected_review_case_ids,
            )
        finally:
            self._flush_affected_review_case_ids(affected_review_case_ids)

    def _collect_capability_readiness(
        self,
        libraries: tuple[str, ...],
        *,
        force: bool,
        affected_review_case_ids: set[str],
    ) -> list[dict[str, object]]:
        """Project least-authority readiness before consent can be granted."""
        if self._activity_only:
            # The live field application persists every negative observation as
            # a suspension and clears that suspension only after an allowed
            # observation.  The dedicated reader therefore preserves that
            # state by treating an unsuspended destination as allowed.  It
            # grants no authority: ``enable`` force-probes the live capability
            # again before consent can be committed.
            results: list[dict[str, object]] = []
            for connection, library in self._libraries:
                if library not in libraries:
                    continue
                destinations: tuple[tuple[str, str], ...] | list[dict[str, str]] = (
                    tuple(
                        (
                            destination,
                            self._repository.field_effect_capability_suspension(
                                connection, destination
                            )
                            or "allowed",
                        )
                        for destination in sorted(SUPPORTED_DESTINATIONS)
                    )
                )
                results.append(
                    {
                        "connection": connection,
                        "library": library,
                        "state": (
                            "connection_unavailable"
                            if any(
                                state == "connection_unavailable"
                                for _, state in destinations
                            )
                            else "allowed"
                            if all(state == "allowed" for _, state in destinations)
                            else "partially_allowed"
                            if any(state == "allowed" for _, state in destinations)
                            else "not_ready"
                        ),
                        "destinations": [
                            {"destination": destination, "state": state}
                            for destination, state in destinations
                        ],
                    }
                )
            return results
        now = self._clock()
        representatives: dict[str, str] = {}
        cursor: tuple[int, str] | None = None
        while len(representatives) < len(libraries):
            documents = self._repository.pending_field_effect_decision_documents(
                100, cursor
            )
            if not documents:
                break
            for document in documents:
                decision = self._decision(document)
                cursor = (decision.occurred_at, decision.decision_id)
                try:
                    snapshot = self._authority.resolve(decision)
                except (FieldEffectError, GuardedApplicationError, ValueError) as error:
                    # Same rule, higher stakes. A library left without a
                    # representative is reported `not_ready` across every
                    # destination, and that verdict is written to the 30-second
                    # capability cache below, so a fault absorbed here would be
                    # served as a false negative to callers that never met it.
                    # Raising from this loop keeps it out of the cache as well
                    # as out of the response: nothing is written until after.
                    if getattr(error, "code", None) == "persistence":
                        raise
                    continue
                if snapshot.library in libraries:
                    representatives.setdefault(snapshot.library, snapshot.work_id)
        results = []
        for connection, library in self._libraries:
            if library not in libraries:
                continue
            with self._capability_lock:
                cached = self._capability_cache.get(library)
            if not force and cached is not None and now - cached[0] <= 30:
                results.append(
                    {
                        **cached[1],
                        "destinations": [
                            {"destination": destination, "state": state}
                            for destination, state in cast(
                                "tuple[tuple[str, str], ...]", cached[1]["destinations"]
                            )
                        ],
                    }
                )
                continue
            work_id = representatives.get(library)
            destinations = []
            state = "not_ready"
            if work_id is not None:
                observer = self._capabilities[(connection, library)]
                for destination in sorted(SUPPORTED_DESTINATIONS):
                    observed_at = self._clock()
                    observed = observer.observe(_CapabilityProbe(work_id, destination))  # type: ignore[arg-type]
                    destinations.append(
                        {
                            "destination": destination,
                            "state": observed.category,
                        }
                    )
                    if observed.category == "allowed":
                        prior_suspension = (
                            self._repository.field_effect_capability_suspension(
                                connection, destination
                            )
                        )
                        if not self._activity_only:
                            for reason in (
                                "insufficient_permission",
                                "field_unavailable",
                            ):
                                self._repository.clear_field_effect_capability_suspension(
                                    scope="destination",
                                    connection=connection,
                                    destination=destination,
                                    reason=reason,
                                    observed_at=observed_at,
                                )
                            self._repository.clear_field_effect_capability_suspension(
                                scope="connection",
                                connection=connection,
                                destination="",
                                reason="connection_unavailable",
                                observed_at=observed_at,
                            )
                        remaining = self._repository.field_effect_capability_suspension(
                            connection, destination
                        )
                        if prior_suspension is not None and remaining is None:
                            self.wake()
                        if remaining is not None:
                            destinations[-1]["state"] = remaining
                    elif observed.category == "connection_unavailable":
                        if not self._activity_only:
                            affected_review_case_ids.update(
                                self._repository.suspend_field_effect_capability(
                                    scope="connection",
                                    connection=connection,
                                    destination=destination,
                                    reason=observed.category,
                                    changed_at=observed_at,
                                )
                            )
                        destinations = [
                            {"destination": name, "state": "connection_unavailable"}
                            for name in sorted(SUPPORTED_DESTINATIONS)
                        ]
                        break
                    else:
                        if not self._activity_only:
                            self._repository.clear_field_effect_capability_suspension(
                                scope="connection",
                                connection=connection,
                                destination="",
                                reason="connection_unavailable",
                                observed_at=observed_at,
                            )
                            affected_review_case_ids.update(
                                self._repository.suspend_field_effect_capability(
                                    scope="destination",
                                    connection=connection,
                                    destination=destination,
                                    reason=observed.category,
                                    changed_at=observed_at,
                                )
                            )
                        remaining = self._repository.field_effect_capability_suspension(
                            connection, destination
                        )
                        if remaining is not None:
                            destinations[-1]["state"] = remaining
                if any(
                    value["state"] == "connection_unavailable" for value in destinations
                ):
                    state = "connection_unavailable"
                elif all(value["state"] == "allowed" for value in destinations):
                    state = "allowed"
                elif any(value["state"] == "allowed" for value in destinations):
                    state = "partially_allowed"
                else:
                    state = "not_ready"
            else:
                destinations = [
                    {"destination": destination, "state": "not_ready"}
                    for destination in sorted(SUPPORTED_DESTINATIONS)
                ]
            cached_result = {
                "connection": connection,
                "library": library,
                "state": state,
                "destinations": tuple(
                    (str(value["destination"]), str(value["state"]))
                    for value in destinations
                ),
            }
            with self._capability_lock:
                self._capability_cache[library] = (
                    now,
                    cast("dict[str, object]", cached_result),
                )
            results.append(
                {
                    **cached_result,
                    "destinations": [
                        {"destination": destination, "state": destination_state}
                        for destination, destination_state in cached_result[
                            "destinations"
                        ]
                    ],
                }
            )
        return results

    def _run(self) -> None:
        with (
            nullcontext()
            if self._operation_admission is None
            else self._operation_admission.classify("background")
        ):
            self._admission.wait()
            if self._stop.is_set():
                return
            if self._dormant_start:
                self._dormant_start = False
                try:
                    self._recover_startup()
                except Exception as error:
                    self._set_last_failure(getattr(error, "code", "unavailable"))
            while not self._stop.is_set():
                self._wake.wait(self._polling_seconds)
                self._wake.clear()
                try:
                    self._run_until_idle()
                except (FieldEffectError, GuardedApplicationError, ValueError) as error:
                    self._set_last_failure(getattr(error, "code", "unavailable"))
                else:
                    # A completed poll recovers a transient worker/storage fault,
                    # not a rejected decision still pending behind the cursor.
                    self._set_last_failure(
                        self._failed_decision[1] if self._failed_decision else None
                    )

    def _run_until_idle(self) -> None:
        with (
            nullcontext()
            if self._operation_admission is None
            else self._operation_admission.classify("background")
        ):
            next(iter(self._applications.values())).run_pending_cleanups(limit=100)
            # A read-back that failed mid-run leaves a queued targeted read that
            # no cycle selects; resolve one page of them per pass rather than
            # wait for a restart (#1382). One page, not the restart drain: a
            # third value queues a re-evaluation for the same plan, which the
            # next pass takes. A failure is reported after the cycle runs.
            failures: list[Exception] = []
            if not self._stop.is_set():
                for plan_id in self._repository.pending_field_effect_action_plan_ids(
                    100
                ):
                    try:
                        self.reconcile(plan_id)
                    except Exception as error:
                        failures.append(error)
            if not self._stop.is_set():
                self._observe_waiting_connections()
            while not self._stop.is_set() and self.run_cycle():
                pass
            if failures:
                raise GuardedApplicationError("recovery") from failures[0]

    def _release_completed_manual_returns(self) -> None:
        if self._scan_progress is None:
            return
        pending = self._repository.pending_field_effect_manual_returns(
            100, self._manual_return_cursor
        )
        for row in pending:
            if (
                type(row) is not tuple
                or len(row) != 8
                or any(type(value) is not str for value in row[:6])
                or type(row[6]) is not int
                or type(row[7]) is not int
            ):
                raise GuardedApplicationError("persistence")
            (
                review_case_id,
                destination,
                connection,
                library,
                hold_token,
                scan_id,
                requested_at,
                _updated_at,
            ) = row
            if (connection, library) not in self._applications:
                continue
            release = _PendingManualReturn(
                (connection, library), hold_token, scan_id, requested_at
            )
            try:
                progress = self._scan_progress(*release.context)
            except Exception as error:
                # Same injected, untyped seam as the manual-return request. A
                # progress reading that did not arrive proves nothing about the
                # scan, so it must raise rather than let the pending return be
                # released against an unobserved state.
                raise GuardedApplicationError("scan") from error
            if (
                getattr(progress, "scan_id", None) != release.scan_id
                or getattr(progress, "state", None) != "completed"
                or getattr(progress, "rescan_requested", None) is not False
                or type(getattr(progress, "updated_at", None)) is not int
                or cast(_ScanProgress, progress).updated_at < release.requested_at
            ):
                continue
            cleared = self._repository.clear_field_effect_manual_hold(
                review_case_id, destination, self._clock(), release.hold_token
            )
            if cleared is not None:
                self._restart_schedule()
        self._manual_return_cursor = (
            (pending[-1][7], pending[-1][0], pending[-1][1])
            if len(pending) == 100
            else None
        )

    def _application(self, operation: FieldEffectOperation) -> GuardedFieldApplication:
        application = self._applications.get(
            (operation.plan.connection, operation.plan.library)
        )
        if application is None:
            raise GuardedApplicationError("configuration")
        return application

    @staticmethod
    def _decision(
        document: str,
    ) -> (
        HumanDecision
        | AutomaticDecision
        | CoverAutomaticDecision
        | AuthoredDescriptionDecision
    ):
        raw = json.loads(document)
        if type(raw) is not dict:
            raise FieldEffectError("authority_binding")
        if raw.get("schema_version") == "authored-description-decision-v1":
            return AuthoredDescriptionDecision.from_json(document)
        if raw.get("schema_version") == "human-decision-v3":
            return HumanDecision.from_json(document)
        if raw.get("schema_version") == "automatic-decision-v1":
            return AutomaticDecision.from_json(document)
        if raw.get("schema_version") == "cover-automatic-decision-v3":
            return parse_automatic_authority(document)
        raise FieldEffectError("authority_binding")
