"""Continuous, fair activation around the guarded field-effect kernel."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol, cast

from mechanical_librarian.application.field_effects import (
    AuthorityCurrentness,
    CapabilityObserver,
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    SUPPORTED_DESTINATIONS,
    FieldEffectError,
    FieldEffectOperation,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)


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
    """Complete application-owned persistence contract for guarded work."""

    def pending_field_effect_plan_ids(self, limit: int) -> tuple[str, ...]: ...
    def pending_field_effect_action_plan_ids(self, limit: int) -> tuple[str, ...]: ...
    def field_effect_backup_ready(self) -> bool: ...
    def pending_field_effect_decision_counts(self) -> tuple[dict[str, object], ...]: ...
    def grant_field_effect_consent_matrix(self, *values: object) -> object: ...
    def revoke_library_field_effect_consents(self, *values: object) -> object: ...
    def field_effect_manual_override(self, *values: object) -> object: ...
    def field_effect_manual_hold_context(self, *values: object) -> tuple[str, str] | None: ...
    def field_effect_manual_hold_token(self, *values: object) -> str | None: ...
    def request_field_effect_manual_return(self, *values: object) -> bool: ...
    def get_field_effect(self, plan_id: str) -> FieldEffectOperation: ...
    def pending_field_effect_decision_documents(self, limit: int, after: object = None) -> tuple[str, ...]: ...
    def record_current_field_effect_no_change(self, decision: object, occurred_at: int) -> bool: ...
    def retire_stale_cover_decision(self, *values: object) -> bool: ...
    def current_field_effect_consent(self, *values: object) -> object: ...
    def suspend_field_effect_capability(self, **values: object) -> None: ...
    def clear_field_effect_capability_suspension(self, **values: object) -> None: ...
    def field_effect_capability_suspension(self, *values: object) -> str | None: ...
    def next_field_effect_plan_id(
        self, last_library: str | None
    ) -> tuple[str, str] | None: ...
    def field_effect_activity_data(self, limit: int) -> dict[str, object]: ...
    def field_effect_activity_summary(self) -> dict[str, object]: ...

    def field_effect_activity_page(
        self, *, search: str | None, event_type: str, occurred_after: int,
        page: int, page_size: int,
    ) -> dict[str, object]: ...
    def pending_field_effect_manual_returns(
        self, limit: int, after: object = None
    ) -> tuple[tuple[str, str, str, str, str, str, int, int], ...]: ...
    def clear_field_effect_manual_hold(self, *values: object, **kwargs: object) -> tuple[str, str] | None: ...


class ContinuousGuardedApplication:
    """Compose persisted decisions into one instance-scoped mutation slot."""

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
        graceful_shutdown_seconds: int = 30,
        scan_progress: Callable[[str, str], object] | None = None,
        activity_only: bool = False,
        provider_recovery_snapshot: Callable[[], tuple[dict[str, int | str | None], ...]] | None = None,
    ) -> None:
        if ((activity_only and (applications or capabilities))
                or (not activity_only and (
                    set(applications) != set(configured_libraries)
                    or set(capabilities) != set(configured_libraries)
                ))
                or not 0.1 <= polling_seconds <= 60.0
                or type(graceful_shutdown_seconds) is not int
                or not 1 <= graceful_shutdown_seconds <= 300
                or type(activity_only) is not bool):
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
        self._graceful_shutdown_seconds = graceful_shutdown_seconds
        self._scan_progress = scan_progress
        self._activity_only = activity_only
        self._provider_recovery_snapshot = provider_recovery_snapshot or (lambda: ())
        self._slot = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_library: str | None = None
        self._last_failure: str | None = None
        self._schedule_cursor: tuple[int, str] | None = None
        self._manual_return_cursor: tuple[int, str, str] | None = None
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

    def close_admission(self) -> None:
        """Stop new claims without waiting for the active worker."""
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
        self._wake.set()

    def enable(
        self, grants: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> dict[str, object]:
        configured = tuple(library for _, library in self._libraries)
        if (type(grants) is not tuple or not grants
                or any(type(grant) is not tuple or len(grant) != 2
                       for grant in grants)):
            raise GuardedApplicationError("consent")
        libraries = tuple(grant[0] for grant in grants)
        submitted = {library: destinations for library, destinations in grants}
        if (len(set(libraries)) != len(libraries)
                or any(type(library) is not str or library not in configured
                       or type(destinations) is not tuple or not destinations
                       or len(set(destinations)) != len(destinations)
                       or any(type(destination) is not str
                              or destination not in SUPPORTED_DESTINATIONS
                              for destination in destinations)
                       for library, destinations in grants)):
            raise GuardedApplicationError("consent")
        if not self._repository.field_effect_backup_ready():
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
                for destination in cast(
                    "list[dict[str, object]]", item["destinations"]
                )
                if destination["state"] == "allowed"
            )
            for item in readiness
        }
        if (any(item["state"] == "connection_unavailable" for item in readiness)
                or any(not eligible[str(item["library"])] for item in readiness)):
            raise GuardedApplicationError("capability")
        if any(
            set(submitted[library]) != set(eligible[library])
            for library in libraries
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
        with self._slot:
            return application.apply(plan_id)

    def reconcile(self, plan_id: str) -> FieldEffectOperation:
        operation = self._repository.get_field_effect(plan_id)
        application = self._application(operation)
        with self._slot:
            return application.reconcile(plan_id)

    def schedule(self, limit: int = 100) -> int:
        self._release_completed_manual_returns()
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
            # This is intentionally before authority, consent and capability: an
            # equal automatic candidate is a terminal decision fact, not a write.
            no_change = getattr(self._repository, "record_current_field_effect_no_change", None)
            if (
                type(decision) is AutomaticDecision
                and no_change is not None
                and no_change(decision, self._clock())
            ):
                continue
            try:
                snapshot = self._authority.resolve(decision)
            except (FieldEffectError, GuardedApplicationError, ValueError):
                if (
                    decision.destination == "cover"
                    and self._repository.retire_stale_cover_decision(
                        decision.decision_id, self._clock()
                    )
                ):
                    continue
                raise
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
            observed_at = self._clock()
            observed = capability.observe(
                _CapabilityProbe(snapshot.work_id, snapshot.destination)  # type: ignore[arg-type]  # reason: observers read only work_id and destination, which the probe supplies; a full FieldEffectPlan does not exist before planning.
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
                    changed_at=observed_at,
                )
                continue
            for reason in ("insufficient_permission", "field_unavailable"):
                self._repository.clear_field_effect_capability_suspension(
                    scope="destination", connection=snapshot.connection,
                    destination=snapshot.destination, reason=reason,
                    observed_at=observed_at,
                )
            self._repository.clear_field_effect_capability_suspension(
                scope="connection", connection=snapshot.connection, destination="",
                reason="connection_unavailable", observed_at=observed_at,
            )
            if self._repository.field_effect_capability_suspension(
                snapshot.connection, snapshot.destination
            ) is not None:
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
            (normalized_search is not None and (not normalized_search or len(normalized_search) > 128))
            or event_type not in {"all", "review", "policy", "metadata", "safety", "unknown"}
            or range_days not in {7, 30, 90}
            or type(page) is not int
            or not 1 <= page <= 1_000_000
            or page_size not in {25, 50, 100}
        ):
            raise GuardedApplicationError("activity_query")
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
            or any(type(key) is not str or type(value) is not str
                   for key, value in report_documents.items())
        ):
            raise GuardedApplicationError("activity")
        reports: dict[str, MetadataReport] = {}
        current_waiting = data.get("current_waiting")
        if current_waiting is not None and (
            type(current_waiting) is not dict
            or set(current_waiting) != {"destination", "library", "state", "work_id", "work_label"}
            or any(type(value) is not str or len(value) > 512
                   for value in current_waiting.values())
            or current_waiting["state"] not in {"planned", "claimed", "applying", "reconciling", "retryable"}
        ):
            raise GuardedApplicationError("activity")
        for event in events:
            if type(event) is not dict:
                raise GuardedApplicationError("activity")
            # The persistence projection carries these private join values
            # only long enough to derive a closed, readable Activity display.
            # Keeping old in-memory test projections value-free is intentional:
            # they are already a public-shaped activity record, not a report
            # lineage assertion.
            connection = event.pop("connection", None)
            report_id = event.pop("report_id", None)
            event["work_label"] = event.get("work_label", "Recorded work")
            event["author_label"] = "Author details are not recorded"
            if connection is None and report_id is None:
                continue
            if type(connection) is not str or type(report_id) is not str:
                raise GuardedApplicationError("activity")
            report = reports.get(report_id)
            if report is None:
                report_document = report_documents.get(report_id)
                if type(report_document) is not str:
                    raise GuardedApplicationError("activity")
                try:
                    report = MetadataReport.from_json(report_document)
                except Exception as error:
                    raise GuardedApplicationError("activity") from error
                if report.report_id != report_id:
                    raise GuardedApplicationError("activity")
                reports[report_id] = report
            if (report.target.connection != connection
                    or report.target.library != event.get("library")
                    or report.target.item != event.get("work_id")):
                raise GuardedApplicationError("activity")
            event["work_label"] = report.target.current_title or "Untitled"
            authors = next((field.value for field in report.target.fields if field.destination is TargetDestination.AUTHORS), ())
            if type(authors) is tuple and all(type(author) is str for author in authors):
                joined = ", ".join(cast("tuple[str, ...]", authors))
                event["author_label"] = (
                    joined if len(joined) <= 512 else joined[:511].rstrip() + "…"
                ) or "Author details are not recorded"
        cleanup = data.pop("cleanup", None)
        if cleanup is not None and (
            type(cleanup) is not dict
            or set(cleanup) != {"pending", "exhausted"}
            or any(type(value) is not int or value < 0 for value in cleanup.values())
        ):
            raise GuardedApplicationError("activity")
        consented = {
            (
                str(value["connection"]), str(value["library"]),
                str(value["destination"]),
            )
            for value in cast(
                "list[dict[str, object]]",
                data.pop("consented_destinations", []),
            )
        }
        documents = self._repository.pending_field_effect_decision_documents(100)
        prepared_rows = self._repository.pending_field_effect_decision_counts()
        prepared_by_library: dict[str, dict[str, int]] = {
            library: {"automatic": 0, "human": 0, "total": 0}
            for _, library in self._libraries
        }
        for item in prepared_rows:
            library = str(item["library"])
            origin = str(item["origin"])
            count = int(cast(int, item["count"]))
            values = prepared_by_library.setdefault(
                library, {"automatic": 0, "human": 0, "total": 0}
            )
            values[origin] += count
            values["total"] += count
        example: dict[str, object] | None = None
        for document in documents:
            decision = self._decision(document)
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
                destination["consented"] = (
                    connection, library, str(destination["destination"])
                ) in consented
        prepared = {
            "automatic": sum(value["automatic"] for value in prepared_by_library.values()),
            "human": sum(value["human"] for value in prepared_by_library.values()),
            "total": sum(value["total"] for value in prepared_by_library.values()),
        }
        provider_recovery = list(self._provider_recovery_snapshot())
        if len(provider_recovery) > 100 or any(
            type(item) is not dict or set(item) != {"state", "next_eligible_at", "affected_count"}
            or item["state"] not in {"waiting", "checking", "dormant", "recovered", "fanout", "exhausted"}
            or type(item["affected_count"]) is not int or not 1 <= item["affected_count"] <= 1_000_000
            or (item["next_eligible_at"] is not None and (type(item["next_eligible_at"]) is not int or not 0 <= item["next_eligible_at"] <= 8_640_000_000_000))
            for item in provider_recovery
        ):
            raise GuardedApplicationError("activity")
        return {
            **data,
            "backup_ready": self._repository.field_effect_backup_ready(),
            "configured_libraries": [
                {"connection": connection, "library": library,
                 "prepared": prepared_by_library[library]}
                for connection, library in self._libraries
            ],
            "capabilities": capabilities,
            "enabled_libraries": enabled,
            "example": example,
            "last_failure": self._last_failure,
            "prepared": prepared,
            "ledger": {
                "event_type": event_type,
                "page": int(cast(int, page_projection["page"])),
                "page_count": int(cast(int, page_projection["page_count"])),
                "page_size": int(cast(int, page_projection["page_size"])),
                "query": normalized_search,
                "range_days": range_days,
                "total": int(cast(int, page_projection["total"])),
            },
            "schema_version": "continuous-field-activity-v2",
            "provider_recovery": provider_recovery,
        }

    def _capability_readiness(
        self, libraries: tuple[str, ...], *, force: bool,
    ) -> list[dict[str, object]]:
        """Project least-authority readiness before consent can be granted."""
        if self._activity_only:
            results: list[dict[str, object]] = []
            for connection, library in self._libraries:
                if library not in libraries:
                    continue
                destinations: tuple[tuple[str, str], ...] | list[dict[str, str]] = tuple(
                    (
                        destination,
                        self._repository.field_effect_capability_suspension(
                            connection, destination
                        ) or "not_ready",
                    )
                    for destination in sorted(SUPPORTED_DESTINATIONS)
                )
                results.append({
                    "connection": connection,
                    "library": library,
                    "state": (
                        "connection_unavailable"
                        if any(state == "connection_unavailable"
                               for _, state in destinations)
                        else "not_ready"
                    ),
                    "destinations": [
                        {"destination": destination, "state": state}
                        for destination, state in destinations
                    ],
                })
            return results
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
        results = []
        for connection, library in self._libraries:
            if library not in libraries:
                continue
            with self._capability_lock:
                cached = self._capability_cache.get(library)
            if not force and cached is not None and now - cached[0] <= 30:
                results.append({
                    **cached[1],
                    "destinations": [
                        {"destination": destination, "state": state}
                        for destination, state in cast(
                            "tuple[tuple[str, str], ...]", cached[1]["destinations"]
                        )
                    ],
                })
                continue
            work_id = representatives.get(library)
            destinations = []
            state = "not_ready"
            if work_id is not None:
                observer = self._capabilities[(connection, library)]
                for destination in sorted(SUPPORTED_DESTINATIONS):
                    observed_at = self._clock()
                    observed = observer.observe(_CapabilityProbe(work_id, destination))  # type: ignore[arg-type]
                    destinations.append({
                        "destination": destination, "state": observed.category,
                    })
                    if observed.category == "allowed":
                        if not self._activity_only:
                            for reason in ("insufficient_permission", "field_unavailable"):
                                self._repository.clear_field_effect_capability_suspension(
                                    scope="destination", connection=connection,
                                    destination=destination, reason=reason,
                                    observed_at=observed_at,
                                )
                            self._repository.clear_field_effect_capability_suspension(
                                scope="connection", connection=connection, destination="",
                                reason="connection_unavailable", observed_at=observed_at,
                            )
                        remaining = self._repository.field_effect_capability_suspension(
                            connection, destination
                        )
                        if remaining is not None:
                            destinations[-1]["state"] = remaining
                    elif observed.category == "connection_unavailable":
                        if not self._activity_only:
                            self._repository.suspend_field_effect_capability(
                                scope="connection", connection=connection,
                                destination=destination, reason=observed.category,
                                changed_at=observed_at,
                            )
                        destinations = [
                            {"destination": name, "state": "connection_unavailable"}
                            for name in sorted(SUPPORTED_DESTINATIONS)
                        ]
                        break
                    else:
                        if not self._activity_only:
                            self._repository.clear_field_effect_capability_suspension(
                                scope="connection", connection=connection, destination="",
                                reason="connection_unavailable", observed_at=observed_at,
                            )
                            self._repository.suspend_field_effect_capability(
                                scope="destination", connection=connection,
                                destination=destination, reason=observed.category,
                                changed_at=observed_at,
                            )
                        remaining = self._repository.field_effect_capability_suspension(
                            connection, destination
                        )
                        if remaining is not None:
                            destinations[-1]["state"] = remaining
                if any(value["state"] == "connection_unavailable"
                       for value in destinations):
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
                "connection": connection, "library": library, "state": state,
                "destinations": tuple(
                    (str(value["destination"]), str(value["state"]))
                    for value in destinations
                ),
            }
            with self._capability_lock:
                self._capability_cache[library] = (
                    now, cast("dict[str, object]", cached_result),
                )
            results.append({
                **cached_result,
                "destinations": [
                    {"destination": destination, "state": destination_state}
                    for destination, destination_state
                    in cached_result["destinations"]
                ],
            })
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
        next(iter(self._applications.values())).run_pending_cleanups(limit=100)
        while not self._stop.is_set() and self.run_cycle():
            pass

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
                review_case_id, destination, connection, library,
                hold_token, scan_id, requested_at, _updated_at,
            ) = row
            if (connection, library) not in self._applications:
                continue
            release = _PendingManualReturn(
                (connection, library), hold_token, scan_id, requested_at
            )
            try:
                progress = self._scan_progress(*release.context)
            except Exception as error:
                raise GuardedApplicationError("scan") from error
            if (
                getattr(progress, "scan_id", None) != release.scan_id
                or getattr(progress, "state", None) != "completed"
                or getattr(progress, "rescan_requested", None) is not False
                or type(getattr(progress, "updated_at", None)) is not int
                or cast(_ScanProgress, progress).updated_at < release.requested_at
            ):
                continue
            self._repository.clear_field_effect_manual_hold(
                review_case_id, destination, self._clock(), release.hold_token
            )
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
    def _decision(document: str) -> HumanDecision | AutomaticDecision:
        raw = json.loads(document)
        if type(raw) is not dict:
            raise FieldEffectError("authority_binding")
        if raw.get("schema_version") == "human-decision-v1":
            return HumanDecision.from_json(document)
        if raw.get("schema_version") == "automatic-decision-v1":
            return AutomaticDecision.from_json(document)
        raise FieldEffectError("authority_binding")
