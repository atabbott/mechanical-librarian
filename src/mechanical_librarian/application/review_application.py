"""Composed authenticated-browser operations over one serialized state owner."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, cast

from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationError,
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.live_updates import LiveUpdateChannel
from mechanical_librarian.application.metadata_policy import (
    AutomaticSafetyControls,
    AutomaticSafetyRepository,
    PolicyActivation,
    PolicyImpactRepository,
    PolicyNotebook,
    PolicyPreview,
    PolicyTransition,
)
from mechanical_librarian.application.operations import (
    OperationalStatus,
    StageTimer,
    SupportExporter,
    TimingRepository,
)
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.application.review_pipeline import (
    ReviewPreparationSupervisor,
    is_first_boot_scan_id,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    CurrentReviewCaseStore,
    HumanDecisionStore,
    InspectCurrentReviewCase,
    OwnerSessionService,
    RecordHumanDecision,
    ReviewLibraryStore,
    ReviewSessionStore,
    SessionTokens,
)
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
from mechanical_librarian.domain.description_advice import DescriptionAdviceBinding
from mechanical_librarian.domain.field_effect import SUPPORTED_DESTINATIONS
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    OwnerSettingsError,
    SecretWrite,
)
from mechanical_librarian.domain.preparation_presentation import (
    PreparationPresentation,
    PreparationPresentationError,
    PreparationTrigger,
)

if TYPE_CHECKING:
    from mechanical_librarian.application.cover_consideration import (
        _ObservationRequest,
    )

_Result = TypeVar("_Result")


class SerializedGate(Protocol):
    """One serializing call gate that preserves its operation's return type."""

    def __call__(self, call: Callable[[], _Result]) -> _Result: ...


class ReviewApplicationError(RuntimeError):
    """A fixed, value-free application-boundary failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReviewPolicyStore(PolicyImpactRepository, AutomaticSafetyRepository, Protocol):
    """Both durable policy seams the served composition binds to one store.

    `compose_review_application` builds a notebook and the owner-only safety
    controls from the same argument, so the parameter names both contracts
    rather than leaving the second one to a runtime failure.
    """


class BrowserStateOwner(Protocol):
    """Composition-only lifecycle and status capabilities of the SQLite owner."""

    def close(self) -> None: ...

    def operational_snapshot(self) -> dict[str, object]: ...

    def field_effect_capability_suspension(
        self, connection: str, destination: str
    ) -> str | None: ...

    def preparation_presentation(self, now: int) -> PreparationPresentation | None: ...

    def dismiss_preparation_presentation(
        self, run_id: str, dismissed_at: int
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CaseDetailView:
    comparison_notices: tuple[str, ...]
    current: bool
    fields: tuple[dict[str, object], ...]
    # What is wrong with the media itself, carried beside the fields rather
    # than inside one: no target field states it, and nothing here is a
    # proposal or a decision.
    media_health: tuple[dict[str, object], ...]
    review_case_id: str
    schema_version: str
    target: dict[str, object]
    writes_consented: bool
    recording_identity_basis: dict[str, str] | None

    @classmethod
    def from_data(cls, value: dict[str, object]) -> "CaseDetailView":
        return cls(
            tuple(
                str(item) for item in cast("list[object]", value["comparison_notices"])
            ),
            bool(value["current"]),
            tuple(
                dict(item) for item in cast("list[dict[str, object]]", value["fields"])
            ),
            tuple(
                dict(item)
                for item in cast("list[dict[str, object]]", value["media_health"])
            ),
            str(value["review_case_id"]),
            str(value["schema_version"]),
            dict(cast("dict[str, object]", value["target"])),
            bool(value["writes_consented"]),
            None
            if value["recording_identity_basis"] is None
            else dict(cast("dict[str, str]", value["recording_identity_basis"])),
        )

    def to_data(self) -> dict[str, object]:
        return {
            "comparison_notices": list(self.comparison_notices),
            "current": self.current,
            "fields": [dict(item) for item in self.fields],
            "media_health": [dict(item) for item in self.media_health],
            "review_case_id": self.review_case_id,
            "schema_version": self.schema_version,
            "target": dict(self.target),
            "writes_consented": self.writes_consented,
            "recording_identity_basis": self.recording_identity_basis,
        }


@dataclass(frozen=True, slots=True)
class LibraryView:
    counts: dict[str, int]
    items: tuple[dict[str, object], ...]
    lens: str
    next_cursor: str | None
    page: int
    previous_cursor: str | None
    query: str | None
    schema_version: str

    @classmethod
    def from_data(cls, value: dict[str, object]) -> "LibraryView":
        counts = value["counts"]
        items = value["items"]
        assert isinstance(counts, dict)
        assert isinstance(items, list)
        return cls(
            {str(key): int(count) for key, count in counts.items()},
            tuple(dict(item) for item in items),
            str(value["lens"]),
            value["next_cursor"] if isinstance(value["next_cursor"], str) else None,
            int(cast("int", value["page"])),
            value["previous_cursor"]
            if isinstance(value["previous_cursor"], str)
            else None,
            value["query"] if isinstance(value["query"], str) else None,
            str(value["schema_version"]),
        )

    def to_data(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts),
            "items": [dict(item) for item in self.items],
            "lens": self.lens,
            "next_cursor": self.next_cursor,
            "page": self.page,
            "previous_cursor": self.previous_cursor,
            "query": self.query,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class AttentionView:
    items: tuple[dict[str, object], ...]
    next_cursor: str | None
    query: str | None
    schema_version: str
    total: int

    @classmethod
    def from_data(cls, value: dict[str, object]) -> "AttentionView":
        items = value["items"]
        assert isinstance(items, list)
        return cls(
            tuple(dict(item) for item in items),
            value["next_cursor"] if isinstance(value["next_cursor"], str) else None,
            value["query"] if isinstance(value["query"], str) else None,
            str(value["schema_version"]),
            int(cast(int, value["total"])),
        )

    def to_data(self) -> dict[str, object]:
        return {
            "items": [dict(item) for item in self.items],
            "next_cursor": self.next_cursor,
            "query": self.query,
            "schema_version": self.schema_version,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class PreparationView:
    libraries: tuple[dict[str, object], ...]
    paused: bool
    active: bool
    actionable: bool
    connections: tuple[dict[str, object], ...] = ()
    first_boot: bool = False
    first_boot_complete: bool = False
    first_boot_works: int | None = None
    presentation: dict[str, object] | None = None
    schema_version: str = "review-preparation-v2"

    def to_data(self) -> dict[str, object]:
        return {
            "libraries": [dict(item) for item in self.libraries],
            "paused": self.paused,
            "active": self.active,
            "actionable": self.actionable,
            "first_boot": self.first_boot,
            "first_boot_complete": self.first_boot_complete,
            "first_boot_works": self.first_boot_works,
            "connections": [dict(item) for item in self.connections],
            "presentation": None
            if self.presentation is None
            else dict(self.presentation),
            "schema_version": self.schema_version,
        }


class PolicyOperations:
    """Serialized named policy operations; transport never receives its store."""

    def __init__(self, notebook: PolicyNotebook, gate: SerializedGate) -> None:
        self._notebook = notebook
        self._gate = gate

    def active(self) -> MetadataPolicyProfile:
        return self._gate(self._notebook.active)

    def rich_qualification(
        self, profile: MetadataPolicyProfile
    ) -> tuple[dict[str, object], ...]:
        return self._gate(lambda: self._notebook.rich_qualification(profile))

    def audiobook_qualification(
        self, profile: MetadataPolicyProfile
    ) -> tuple[dict[str, object], ...]:
        return self._gate(lambda: self._notebook.audiobook_qualification(profile))

    def validate(self, profile: MetadataPolicyProfile) -> MetadataPolicyProfile:
        return self._gate(lambda: self._notebook.validate(profile))

    def create(
        self, name: object, options: object, destinations: object
    ) -> MetadataPolicyProfile:
        return self._gate(lambda: self._notebook.create(name, options, destinations))

    def read(self, policy_id: object) -> MetadataPolicyProfile:
        return self._gate(lambda: self._notebook.read(policy_id))

    def preview(self, profile: MetadataPolicyProfile) -> PolicyPreview:
        return self._gate(lambda: self._notebook.preview(profile))

    def shadow_state(self) -> dict[str, object]:
        return self._gate(self._notebook.shadow_state)

    def confirm_automatic(self, updated_at: object) -> dict[str, object]:
        return self._gate(lambda: self._notebook.confirm_automatic(updated_at))

    def enter_preview(self, updated_at: object) -> dict[str, object]:
        return self._gate(lambda: self._notebook.enter_preview(updated_at))

    def preview_leave(self) -> dict[str, object]:
        return self._gate(self._notebook.preview_leave)

    def shadow_sample(self, limit: object = 20) -> dict[str, object]:
        return self._gate(lambda: self._notebook.shadow_sample(limit))

    def activate(
        self, policy_id: object, expected_active_policy_id: object, activated_at: object
    ) -> PolicyActivation:
        return self._gate(
            lambda: self._notebook.activate(
                policy_id, expected_active_policy_id, activated_at
            )
        )

    def transition(self) -> PolicyTransition | None:
        return self._gate(self._notebook.transition)

    def retry(self, transition_id: object, retried_at: object) -> PolicyTransition:
        return self._gate(lambda: self._notebook.retry(transition_id, retried_at))

    def acknowledge(
        self, transition_id: object, acknowledged_at: object
    ) -> PolicyTransition:
        return self._gate(
            lambda: self._notebook.acknowledge(transition_id, acknowledged_at)
        )


class AutomaticSafetyOperations:
    def __init__(
        self,
        controls: AutomaticSafetyControls,
        gate: SerializedGate,
    ) -> None:
        self._controls = controls
        self._gate = gate

    def state(self) -> AutomaticSafetyState:
        return self._gate(self._controls.state)

    def pause(self, paused: object, changed_at: object) -> None:
        self._gate(
            lambda: self._controls.pause(cast(bool, paused), cast(int, changed_at))
        )

    def recover(self, changed_at: object, category: object = None) -> None:
        self._gate(
            lambda: self._controls.recover(
                cast(int, changed_at), cast("str | None", category)
            )
        )

    def suspend(
        self, reason: object, changed_at: object, category: object = None
    ) -> None:
        self._gate(
            lambda: self._controls.suspend(
                cast(str, reason), cast(int, changed_at), cast("str | None", category)
            )
        )


class ReviewApplication:
    """One deep application boundary consumed by every HTTP transport route."""

    def __init__(
        self,
        *,
        state_owner: BrowserStateOwner,
        sessions: OwnerSessionService,
        cases: InspectCurrentReviewCase,
        decisions: RecordHumanDecision,
        browse: BrowseReviewLibrary,
        policy: PolicyNotebook,
        automatic_safety: AutomaticSafetyControls,
        clock: Callable[[], int],
        graceful_shutdown_seconds: int,
        close_state_owner: bool,
        preparation: ReviewPreparationSupervisor | None,
        field_application: GuardedFieldApplication
        | ContinuousGuardedApplication
        | None,
        operations: OperationalStatus | None,
        gate: RLock,
        updates: LiveUpdateChannel,
        activity_projection: ContinuousGuardedApplication | None = None,
        close_activity_projection: Callable[[], None] | None = None,
        browse_observation: Callable[[], AbstractContextManager[None]] | None = None,
        close_browse: Callable[[], None] | None = None,
        report_browse: BrowseReviewLibrary | None = None,
        cover_considerations: CoverConsiderationService | None = None,
        settings: OwnerSettingsOperations | None = None,
        reviewer_access: ReviewerAccessOperations | None = None,
        settings_activation: (
            Callable[
                [
                    OwnerSettingsDocument,
                    int,
                    tuple[SecretWrite, ...],
                    Callable[[], object],
                ],
                object,
            ]
            | None
        ) = None,
        first_boot: bool = False,
        acknowledge_first_boot: Callable[[], None] | None = None,
    ) -> None:
        self._updates = updates
        self._state_owner = state_owner
        self._sessions = sessions
        self._cases = cases
        self._decisions = decisions
        self._browse = browse
        self._report_browse = report_browse or browse
        self._browse_gate = gate if browse_observation is None else RLock()
        self._browse_observation = browse_observation or nullcontext
        self._close_browse = close_browse
        self._browse_closed = False
        self._gate = gate
        self._policy = PolicyOperations(policy, self.serialized)
        self._automatic_safety = AutomaticSafetyOperations(
            automatic_safety, self.serialized
        )
        self._clock = clock
        self._graceful_shutdown_seconds = graceful_shutdown_seconds
        self._close_state_owner = close_state_owner
        self._preparation = preparation
        self._field_application = field_application
        self._activity_projection = activity_projection
        self._close_activity_projection = close_activity_projection
        self._operations = operations
        self._cover_considerations = cover_considerations
        self._settings = settings
        self._reviewer_access = reviewer_access
        if reviewer_access is not None:
            reviewer_access.attach_serialization(self.serialized)
            reviewer_access.attach_changes(self._reviewer_changed)
        self._settings_activation = settings_activation
        self._first_boot = first_boot
        self._acknowledge_first_boot = acknowledge_first_boot

    def _reviewer_changed(self) -> None:
        if self._preparation is not None:
            for connection, library, *_ in self._preparation.libraries:
                self._preparation.request(connection, library)
        self._updates.publish("preparation")
        self._updates.publish("case")
        self._updates.publish("library")

    @property
    def updates(self) -> LiveUpdateChannel:
        """The one invalidation channel every owner surface subscribes to."""
        return self._updates

    @property
    def preparation_available(self) -> bool:
        return self._preparation is not None

    @property
    def operations_available(self) -> bool:
        return self._operations is not None

    def now(self) -> int:
        return self._clock()

    def serialized(self, call: Callable[[], _Result]) -> _Result:
        with self._gate:
            return call()

    @property
    def settings_available(self) -> bool:
        return self._settings is not None

    @property
    def reviewer_access(self) -> ReviewerAccessOperations | None:
        return self._reviewer_access

    def owner_settings(self) -> object:
        if self._settings is None:
            raise ReviewApplicationError("settings_unavailable")
        try:
            return self.serialized(self._settings.read)
        except OwnerSettingsError as error:
            raise ReviewApplicationError("settings_unavailable") from error

    def write_owner_settings(
        self, document: object, revision: int, secrets: object
    ) -> object:
        if self._settings is None:
            raise ReviewApplicationError("settings_unavailable")
        settings = self._settings
        try:

            def write() -> object:
                validated = cast("OwnerSettingsDocument", document)
                secret_writes = cast("tuple[SecretWrite, ...]", secrets)
                preferences = validated.cover_preferences
                cover_update = (
                    self._cover_considerations.policy_update(
                        CoverPolicy(
                            avoid_promotional_text=preferences.avoid_promotional_text,
                            avoid_tie_in_branding=preferences.avoid_tie_in_branding,
                            prefer_minimal_composition=preferences.prefer_minimal_composition,
                        )
                    )
                    if self._cover_considerations is not None
                    else None
                )

                def commit() -> object:
                    return settings.write(
                        validated,
                        revision,
                        secret_writes,
                        cover_update,
                        accepted_at=self._clock(),
                    )

                if self._settings_activation is not None:
                    return self._settings_activation(
                        validated, revision, secret_writes, commit
                    )
                return commit()

            return self.serialized(write)
        except OwnerSettingsError as error:
            code = (
                "settings_stale"
                if "stale" in str(error)
                else (
                    "settings_locked"
                    if "locked" in str(error)
                    else (
                        "settings_invalid"
                        if "invalid" in str(error)
                        or "required" in str(error)
                        or "operation" in str(error)
                        else "settings_unavailable"
                    )
                )
            )
            raise ReviewApplicationError(code) from error
        except Exception as error:
            raise ReviewApplicationError("settings_unavailable") from error

    def start(self) -> None:
        if self._operations is not None:
            self._operations.start()
        if self._field_application is not None:
            self._field_application.start()
        if self._preparation is not None:
            self._preparation.start()

    def start_dormant(self) -> None:
        """Perform fallible startup while every scheduler latch stays closed."""
        if self._operations is not None:
            self._operations.start()
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.start_dormant()
        elif self._field_application is not None:
            raise ReviewApplicationError("settings_unavailable")
        if self._preparation is not None:
            self._preparation.start_dormant()

    def admit(self) -> None:
        """Open the already-started generation after no-fail publication."""
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.admit()
        if self._preparation is not None:
            self._preparation.admit()

    def stop_admission(self) -> None:
        """Suspend only scheduler claims while captured HTTP may finish."""
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.suspend_admission()
        if self._preparation is not None:
            self._preparation.suspend_admission()

    def resume_admission(self) -> None:
        """Restore the current generation after an authority commit failure."""
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.resume_admission()
        if self._preparation is not None:
            self._preparation.resume_admission()

    def begin_drain(self) -> None:
        """Stop admitting successor work while captured requests finish."""
        self._updates.close()
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.close_admission()
        if self._preparation is not None:
            self._preparation.begin_drain()
        if self._operations is not None:
            self._operations.begin_drain()
        if self._field_application is not None and not isinstance(
            self._field_application, ContinuousGuardedApplication
        ):
            self._field_application.begin_drain()

    def stop(self) -> None:
        deadline = time.monotonic() + self._graceful_shutdown_seconds
        # Every shutdown step below must be attempted even after an earlier one
        # fails, or a background lane keeps running past the point the owner
        # asked it to stop. The five collectors are broad because the steps are
        # injected components whose raisable sets this composition never sees,
        # and because a step that failed for an unexpected reason is still a
        # step that did not stop its lane. Nothing is discarded: the first
        # failure is what propagates, so a shutdown that did not complete is
        # never reported as one that did, and every later failure is noted on
        # it rather than lost -- a second lane that also failed to stop has to
        # stay visible to whoever reads the first.
        failures: list[Exception] = []
        # First, because a reader that is still being pushed to while its
        # producers stop would be told about a library that is going away. A
        # closed channel ends every open stream, and the browser's own reconnect
        # is what brings them back when the process returns (#641).
        self._updates.close()
        if isinstance(self._field_application, ContinuousGuardedApplication):
            try:
                self._field_application.close_admission()
            except Exception as error:
                failures.append(error)
        if self._preparation is not None:
            try:
                self._preparation.begin_drain()
            except Exception as error:
                failures.append(error)
        if self._operations is not None:
            try:
                self._operations.begin_drain()
            except Exception as error:
                failures.append(error)
        if self._field_application is not None:
            try:
                if isinstance(self._field_application, ContinuousGuardedApplication):
                    self._field_application.wait_for_drain(deadline)
                else:
                    self._field_application.begin_drain()
            except Exception as error:
                failures.append(error)
        if self._preparation is not None:
            try:
                self._preparation.stop(deadline)
            except Exception as error:
                failures.append(error)
        if self._close_browse is not None:
            try:
                with self._browse_gate:
                    self._browse_closed = True
                    self._close_browse()
            except Exception as error:
                failures.append(error)
        if self._close_activity_projection is not None:
            try:
                self._close_activity_projection()
            except Exception as error:
                failures.append(error)
        if self._reviewer_access is not None:
            try:
                self._reviewer_access.close()
            except Exception as error:
                failures.append(error)
        if failures:
            first, *later = failures
            for failure in later:
                first.add_note(f"further shutdown failure: {failure!r}")
            raise first
        if self._close_state_owner:
            self._state_owner.close()

    def ready(self) -> bool:
        return self._operations is None or self._operations.ready()

    # The four session operations below deliberately bypass ``serialized``:
    # the session store owns its dedicated database and lock (#912), so owner
    # sign-in and session checks never queue behind an operation that holds
    # the application gate while it waits on a scan's write transaction.

    def login(self, password: str) -> SessionTokens:
        return self._sessions.login(password)

    def authenticate(self, session_token: str, csrf_token: str | None = None) -> str:
        return self._sessions.authenticate(session_token, csrf_token)

    def issue_browser_csrf(self, session_token: str) -> str:
        return self._sessions.issue_browser_csrf(session_token)

    def logout(self, session_token: str, csrf_token: str) -> None:
        self._sessions.logout(session_token, csrf_token)

    def operations_status(self) -> dict[str, object]:
        if self._operations is None:
            raise RuntimeError("operations unavailable")
        return self._operations.status()

    def support_preview(self) -> dict[str, object]:
        if self._operations is None:
            raise RuntimeError("support unavailable")
        return self._operations.support_preview()

    def support_export(self) -> bytes:
        if self._operations is None:
            raise RuntimeError("support unavailable")
        return self._operations.support_export()

    def create_policy_draft(
        self, name: object, options: object, destinations: object
    ) -> MetadataPolicyProfile:
        candidate = MetadataPolicyProfile.create(name, options, destinations)
        self._policy.validate(candidate)
        return self._policy.create(
            candidate.name,
            dict(candidate.options),
            dict(candidate.destinations),
        )

    def validate_policy(
        self, name: object, options: object, destinations: object
    ) -> MetadataPolicyProfile:
        candidate = MetadataPolicyProfile.create(name, options, destinations)
        return self._policy.validate(candidate)

    def preview_policy(
        self, name: object, options: object, destinations: object
    ) -> tuple[MetadataPolicyProfile, PolicyPreview]:
        candidate = self.validate_policy(name, options, destinations)
        return candidate, self._policy.preview(candidate)

    def case_detail(self, review_case_id: str) -> CaseDetailView:
        def read() -> CaseDetailView:
            detail = self._cases.run(review_case_id)
            target = detail["target"]
            assert isinstance(target, dict)
            for field in cast("list[object]", detail["fields"]):
                assert isinstance(field, dict)
                if isinstance(self._field_application, ContinuousGuardedApplication):
                    suspension = self._state_owner.field_effect_capability_suspension(
                        str(target["connection"]), str(field["destination"])
                    )
                    field["effect_capability"] = (
                        "allowed" if suspension is None else suspension
                    )
                else:
                    field["effect_capability"] = "unavailable"
            detail["writes_consented"] = isinstance(
                self._field_application, ContinuousGuardedApplication
            ) and self._field_application.destinations_consented(
                str(target["connection"]),
                str(target["library"]),
                tuple(
                    str(cast("dict[str, object]", field)["destination"])
                    for field in cast("list[object]", detail["fields"])
                ),
            )
            return CaseDetailView.from_data(detail)

        return self.serialized(read)

    def case_successor(self, review_case_id: str) -> str | None:
        return self.serialized(lambda: self._cases.successor(review_case_id))

    def evidence(
        self, review_case_id: str, limit: int, cursor: str | None
    ) -> dict[str, object]:
        return self.serialized(
            lambda: self._cases.evidence(review_case_id, limit=limit, cursor=cursor)
        )

    def history(self, review_case_id: str) -> dict[str, object]:
        return self.serialized(lambda: self._cases.history(review_case_id))

    def _browse_serialized(
        self,
        call: Callable[[], _Result],
        check_active: Callable[[], None] | None = None,
    ) -> _Result:
        if check_active is not None:
            check_active()
        with self._browse_gate:
            if check_active is not None:
                check_active()
            if self._browse_closed:
                raise ReviewApplicationError("unavailable")
            with self._browse_observation():
                return call()

    def library(
        self,
        *,
        lens: str,
        search: str | None,
        limit: int,
        cursor: str | None,
        check_active: Callable[[], None] | None = None,
    ) -> LibraryView:
        return self._browse_serialized(
            lambda: LibraryView.from_data(
                self._browse.library(
                    lens=lens, search=search, limit=limit, cursor=cursor
                )
            ),
            check_active,
        )

    def attention(
        self,
        *,
        search: str | None,
        limit: int,
        cursor: str | None,
        check_active: Callable[[], None] | None = None,
    ) -> AttentionView:
        return self._browse_serialized(
            lambda: AttentionView.from_data(
                self._browse.attention(search=search, limit=limit, cursor=cursor)
            ),
            check_active,
        )

    def report_detail(
        self, connection: str, library: str, item: str
    ) -> dict[str, object]:
        """Read one item's recorded evidence where no review case exists."""
        return self.serialized(
            lambda: self._report_browse.report_detail(connection, library, item)
        )

    def record_decision(
        self,
        review_case_id: str,
        *,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        actor: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_key: str,
        consideration_id: str | None = None,
    ) -> dict[str, object]:
        if destination == "cover" and (
            self._cover_considerations is None or consideration_id is None
        ):
            raise ReviewApplicationError("cover_consideration_required")

        def record() -> dict[str, object]:
            if destination == "cover":
                assert self._cover_considerations is not None
                assert consideration_id is not None
                self._cover_considerations.validate_decision(
                    review_case_id,
                    consideration_id,
                    outcome,
                    selected_value,
                )
            return self._decisions.run(
                review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=target_fingerprint,
                destination=destination,
                actor=actor,
                outcome=outcome,
                selected_value=selected_value,
                idempotency_key=idempotency_key,
                consideration_id=consideration_id,
            )

        try:
            result = self.serialized(record)
        except CoverConsiderationError as error:
            raise ReviewApplicationError(error.code) from error
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.wake()
        # A recorded decision changes this work's package and can queue a
        # guarded write. Both are announced so a second tab, and the Library row
        # behind this one, see it without waiting for a poll.
        #
        # The positions those announcements were given are returned to the tab
        # that recorded the decision, which is the only tab that can know it
        # caused them. It uses them to recognise its own echo and settle it
        # without a whole-Library read on the decision path (#635), while every
        # frame it did not cause -- a work finishing preparation, a work that
        # could not finish, another tab's decision -- reaches it unchanged.
        # This is a correlation token on the command's own response, not state
        # on the stream: the frame contract stays sequence, topic and subject.
        return {
            **result,
            "invalidation_sequences": [
                self._updates.publish("case", review_case_id),
                self._updates.publish("library", review_case_id),
                self._updates.publish("activity"),
            ],
        }

    def retry_description_advice(self, review_case_id: str) -> dict[str, object]:
        connection, library = self.serialized(
            lambda: self._decisions.retry_description(review_case_id)
        )
        if self._preparation is not None:
            self._preparation.request(connection, library)
        self._updates.publish("case", review_case_id)
        return {"queued": True}

    def select_description_advice(
        self,
        review_case_id: str,
        candidate_id: str,
        binding: DescriptionAdviceBinding,
        idempotency_key: str,
    ) -> dict[str, object]:
        result = self.serialized(
            lambda: self._decisions.select_description(
                review_case_id, candidate_id, binding, idempotency_key
            )
        )
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.wake()
        return {
            **result,
            "invalidation_sequences": [
                self._updates.publish("case", review_case_id),
                self._updates.publish("library", review_case_id),
                self._updates.publish("activity"),
            ],
        }

    def cover_image(
        self, review_case_id: str, consideration_id: str, reference: str
    ) -> bytes:
        if self._cover_considerations is None:
            raise ReviewApplicationError("unavailable")
        try:
            return self.serialized(
                lambda: cast(
                    CoverConsiderationService, self._cover_considerations
                ).image(review_case_id, consideration_id, reference)
            )
        except CoverConsiderationError as error:
            raise ReviewApplicationError(error.code) from error

    def retry_cover_consideration(self, review_case_id: str) -> dict[str, object]:
        return self.prepare_cover_consideration(review_case_id, retry=True)

    def prepare_cover_consideration(
        self, review_case_id: str, *, retry: bool = False
    ) -> dict[str, object]:
        return prepare_cover_consideration(
            self._cases,
            self._cover_considerations,
            review_case_id,
            serialized=self.serialized,
            retry=retry,
        )

    @property
    def policy(self) -> PolicyOperations:
        return self._policy

    @property
    def automatic_safety(self) -> AutomaticSafetyOperations:
        return self._automatic_safety

    def policy_call(self, call: Callable[[], _Result]) -> _Result:
        return self.serialized(call)

    def automatic_state(self) -> dict[str, object]:
        state = self._automatic_safety.state()
        return {
            "paused": state.paused,
            "global_suspension": state.global_suspension,
            "category_suspensions": dict(state.category_suspensions),
        }

    def activity(
        self,
        *,
        search: str | None = None,
        event_type: str = "all",
        range_days: int = 30,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
        def project(application: ContinuousGuardedApplication) -> dict[str, object]:
            return application.activity(
                search=search,
                event_type=event_type,
                range_days=range_days,
                page=page,
                page_size=page_size,
            )

        if self._activity_projection is not None:
            return project(self._activity_projection)
        return self.serialized(lambda: project(self._continuous_application()))

    def activity_transitions(
        self, event_id: str, after_sequence: int | None = None
    ) -> dict[str, object]:
        if self._activity_projection is not None:
            return self._activity_projection.activity_transitions(
                event_id, after_sequence
            )
        return self.serialized(
            lambda: self._continuous_application().activity_transitions(
                event_id, after_sequence
            )
        )

    def enable_activity(
        self, grants: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> dict[str, object]:
        application = self._continuous_application()
        result = self.serialized(lambda: application.enable(grants))
        self._updates.publish("activity")
        return result

    def revoke_activity(self, connection: str, library: str) -> dict[str, object]:
        application = self._continuous_application()
        result = self.serialized(lambda: application.revoke(connection, library))
        self._updates.publish("activity")
        return result

    def return_to_automatic(self, review_case_id: str, destination: str) -> None:
        if destination not in SUPPORTED_DESTINATIONS:
            raise KeyError("not_found")
        application = self._continuous_application()
        self.serialized(
            lambda: application.return_to_automatic(review_case_id, destination)
        )
        self._updates.publish("case", review_case_id)
        self._updates.publish("activity")

    def field_effect_status(self, plan_id: str) -> dict[str, object]:
        return self._field_effect("status", plan_id)

    def apply_field_effect(self, plan_id: str) -> dict[str, object]:
        return self._field_effect("apply", plan_id)

    def reconcile_field_effect(self, plan_id: str) -> dict[str, object]:
        return self._field_effect("reconcile", plan_id)

    def pause_field_effects(self) -> None:
        self._set_field_effect_paused(True)

    def resume_field_effects(self) -> None:
        self._set_field_effect_paused(False)

    def _field_effect(
        self, operation: Literal["status", "apply", "reconcile"], plan_id: str
    ) -> dict[str, object]:
        if self._field_application is None:
            raise ReviewApplicationError("unavailable")
        selected = getattr(self._field_application, operation)
        return self.serialized(lambda: selected(plan_id).to_data())

    def _set_field_effect_paused(self, paused: bool) -> None:
        if self._field_application is None:
            raise ReviewApplicationError("unavailable")
        self.serialized(
            lambda: cast(
                "GuardedFieldApplication | ContinuousGuardedApplication",
                self._field_application,
            ).set_paused(paused)
        )

    def preparation(self) -> PreparationView:
        presentation = self.serialized(
            lambda: self._state_owner.preparation_presentation(self._clock())
        )
        if self._preparation is None:
            return PreparationView(
                (),
                False,
                False,
                False,
                presentation=None if presentation is None else presentation.to_data(),
            )
        progress_values, paused = self.serialized(self._preparation.status)
        if len(progress_values) != len(self._preparation.libraries):
            raise ReviewApplicationError("persistence")
        first_boot = self._first_boot and all(
            progress is None
            or is_first_boot_scan_id(progress.scan_id, connection, library)
            for (connection, library, _library_id), progress in zip(
                self._preparation.libraries, progress_values, strict=True
            )
        )
        conditions = self.serialized(self._preparation.connection_conditions)
        libraries: tuple[dict[str, object], ...] = tuple(
            {
                "scan_id": None if progress is None else progress.scan_id,
                "connection": connection,
                "library": library,
                "library_id": library_id,
                "state": "idle" if progress is None else progress.state,
                "checked": 0 if progress is None else progress.checked,
                "total": 0 if progress is None else progress.total,
                "ready": 0 if progress is None else progress.ready,
                "blocked": 0 if progress is None else progress.blocked,
                "recorded": 0 if progress is None else progress.recorded,
                "rescan_requested": False
                if progress is None
                else progress.rescan_requested,
                "stages": []
                if progress is None
                else [
                    {"stage": stage, "count": count} for stage, count in progress.stages
                ],
            }
            for (connection, library, library_id), progress in zip(
                self._preparation.libraries, progress_values, strict=True
            )
        )
        connections = tuple(
            {
                "connection": condition.connection,
                "code": condition.code,
                "state": condition.state,
                "affected": condition.affected,
                "updated_at": condition.updated_at,
            }
            for condition in conditions
        )
        first_boot_complete = (
            first_boot
            and bool(libraries)
            and all(
                item["state"] in {"completed", "completed_with_failures"}
                for item in libraries
            )
        )
        return PreparationView(
            libraries,
            paused,
            any(
                item["state"] in {"queued", "discovering", "processing"}
                for item in libraries
            ),
            any(item["state"] == "completed_with_failures" for item in libraries),
            connections,
            first_boot,
            first_boot_complete,
            sum(cast("int", item["total"]) for item in libraries)
            if first_boot_complete
            else None,
            None if presentation is None else presentation.to_data(),
        )

    def dismiss_preparation(self, run_id: str) -> PreparationView:
        if type(run_id) is not str or not run_id.startswith("preparation_run_"):
            raise ReviewApplicationError("stale")
        try:
            self.serialized(
                lambda: self._state_owner.dismiss_preparation_presentation(
                    run_id, self._clock()
                )
            )
        except PreparationPresentationError as error:
            code = "blocked" if error.code == "not_dismissible" else "stale"
            raise ReviewApplicationError(code) from error
        self._updates.publish("preparation")
        return self.preparation()

    def acknowledge_first_boot_preparation(self) -> PreparationView:
        if self._acknowledge_first_boot is None:
            raise ReviewApplicationError("preparation_unavailable")
        self.serialized(self._acknowledge_first_boot)
        self._first_boot = False
        return self.preparation()

    def request_review_scan(self, connection: str, library: str) -> dict[str, object]:
        return self._preparation_request("request", connection, library)

    def retry_review_scan(self, connection: str, library: str) -> dict[str, object]:
        return self._preparation_request("retry", connection, library)

    def pause_review_preparation(self) -> PreparationView:
        return self._set_preparation_paused(True)

    def resume_review_preparation(self) -> PreparationView:
        return self._set_preparation_paused(False)

    def check_review_preparation_connection(self, connection: str) -> PreparationView:
        if self._preparation is None:
            raise ReviewApplicationError("unavailable")
        # The preparation pipeline persists its short begin/finish CAS
        # transitions itself.  The potentially slow target probe must not hold
        # this application's global read/decision gate.
        self._preparation.check_connection(connection)
        return self.preparation()

    def _preparation_request(
        self, action: Literal["request", "retry"], connection: str, library: str
    ) -> dict[str, object]:
        if self._preparation is None:
            raise ReviewApplicationError("unavailable")
        selected = getattr(self._preparation, action)
        progress = self.serialized(
            lambda: (
                selected(connection, library)
                if action == "retry"
                else selected(
                    connection,
                    library,
                    trigger=PreparationTrigger.MANUAL_SCAN,
                )
            )
        )
        library_id = next(
            item[2]
            for item in self._preparation.libraries
            if item[:2] == (connection, library)
        )
        return {
            "scan_id": progress.scan_id,
            "connection": progress.connection,
            "library": progress.library,
            "library_id": library_id,
            "state": progress.state,
            "checked": progress.checked,
            "total": progress.total,
            "ready": progress.ready,
            "blocked": progress.blocked,
            "recorded": progress.recorded,
            "rescan_requested": progress.rescan_requested,
            "stages": [
                {"stage": stage, "count": count} for stage, count in progress.stages
            ],
        }

    def _set_preparation_paused(self, paused: bool) -> PreparationView:
        if self._preparation is None:
            raise ReviewApplicationError("unavailable")
        self.serialized(
            lambda: cast(ReviewPreparationSupervisor, self._preparation).set_paused(
                paused
            )
        )
        return self.preparation()

    def _continuous_application(self) -> ContinuousGuardedApplication:
        if not isinstance(self._field_application, ContinuousGuardedApplication):
            raise ReviewApplicationError("unavailable")
        return self._field_application


def prepare_cover_consideration(
    cases: InspectCurrentReviewCase | None,
    cover_considerations: CoverConsiderationService | None,
    review_case_id: str,
    *,
    serialized: SerializedGate,
    retry: bool = False,
    snapshot: Callable[[], tuple[str, str, bool] | None] | None = None,
    finalize: SerializedGate | None = None,
) -> dict[str, object]:
    """Prepare one exact-current advisory cover consideration.

    Web/CLI requests and ordinary preparation share this operation. Its sole
    retry authority remains the explicit owner retry path.
    """
    if cover_considerations is None:
        raise ReviewApplicationError("unavailable")

    def capture() -> tuple[
        dict[str, object] | None, str, str, str | None, "_ObservationRequest | None"
    ]:
        if snapshot is None:
            if cases is None:
                raise ReviewApplicationError("unavailable")
            detail, report, _ = cases._read(review_case_id)
            cover = next(
                field
                for field in cast("list[dict[str, object]]", detail["fields"])
                if field["destination"] == "cover"
            )
            report_document, proposal_id, has_human_decision = (
                report.to_json(),
                str(cover["proposal_id"]),
                cover["human_decision"] is not None,
            )
        else:
            staged = snapshot()
            if staged is None:
                raise ReviewApplicationError("stale")
            report_document, proposal_id, has_human_decision = staged
        current = cover_considerations.current(review_case_id)
        if retry and has_human_decision and current is not None:
            raise ReviewApplicationError("already_decided")
        existing = (
            None
            if retry
            else cover_considerations.existing(review_case_id, report_document)
        )
        request = (
            None
            if existing is not None
            else cover_considerations.prepare_observation(
                review_case_id, report_document
            )
        )
        return (
            existing,
            report_document,
            proposal_id,
            cover_considerations.latest_id(review_case_id),
            request,
        )

    try:
        existing, report_document, proposal_id, previous_id, request = serialized(
            capture
        )
        if existing is not None:
            return existing
        assert request is not None
        evidence = cover_considerations.observe(request)

        def persist() -> dict[str, object]:
            if snapshot is None:
                if cases is None:
                    raise ReviewApplicationError("unavailable")
                detail, current_report, _ = cases._read(review_case_id)
                cover = next(
                    field
                    for field in cast("list[dict[str, object]]", detail["fields"])
                    if field["destination"] == "cover"
                )
                current_report_document, current_proposal_id, has_human_decision = (
                    current_report.to_json(),
                    str(cover["proposal_id"]),
                    cover["human_decision"] is not None,
                )
            else:
                staged = snapshot()
                if staged is None:
                    raise ReviewApplicationError("stale")
                current_report_document, current_proposal_id, has_human_decision = (
                    staged
                )
            if (
                current_report_document != report_document
                or current_proposal_id != proposal_id
            ):
                raise ReviewApplicationError("stale")
            current = cover_considerations.current(review_case_id)
            if retry and has_human_decision and current is not None:
                raise ReviewApplicationError("already_decided")
            if not retry:
                reopened = cover_considerations.existing(
                    review_case_id, report_document
                )
                if reopened is not None:
                    return reopened
            if cover_considerations.latest_id(review_case_id) != previous_id:
                raise ReviewApplicationError("stale")
            return cover_considerations.ensure(
                review_case_id,
                report_document,
                {"proposal_id": proposal_id},
                retry=retry,
                reviewer_evidence=evidence,
            )

        return (serialized if finalize is None else finalize)(persist)
    except CoverConsiderationError as error:
        raise ReviewApplicationError(error.code) from error


def compose_review_application(
    *,
    state_owner: BrowserStateOwner,
    session_store: ReviewSessionStore,
    case_store: CurrentReviewCaseStore,
    decision_store: HumanDecisionStore,
    library_store: ReviewLibraryStore,
    policy_store: ReviewPolicyStore,
    verify_password: Callable[[str], bool],
    clock: Callable[[], int],
    session_lifetime_seconds: int,
    graceful_shutdown_seconds: int,
    session_digest: Callable[[str], str],
    close_state_owner: bool = False,
    preparation: ReviewPreparationSupervisor | None = None,
    policy_libraries: tuple[tuple[str, dict[str, object] | None], ...] = (),
    field_application: GuardedFieldApplication
    | ContinuousGuardedApplication
    | None = None,
    activity_projection: ContinuousGuardedApplication | None = None,
    close_activity_projection: Callable[[], None] | None = None,
    browse_observation: Callable[[], AbstractContextManager[None]] | None = None,
    close_browse: Callable[[], None] | None = None,
    report_store: ReviewLibraryStore | None = None,
    operational_timings: TimingRepository | None = None,
    operational_support: SupportExporter | None = None,
    operational_timer: StageTimer | None = None,
    cover_considerations: CoverConsiderationService | None = None,
    settings: OwnerSettingsOperations | None = None,
    reviewer_access: ReviewerAccessOperations | None = None,
    settings_activation: (
        Callable[
            [
                OwnerSettingsDocument,
                int,
                tuple[SecretWrite, ...],
                Callable[[], object],
            ],
            object,
        ]
        | None
    ) = None,
    first_boot: bool = False,
    acknowledge_first_boot: Callable[[], None] | None = None,
) -> ReviewApplication:
    """Compose every operation before the HTTP adapter is created."""
    gate = RLock()

    def serialized(call: Callable[[], _Result]) -> _Result:
        with gate:
            return call()

    timing = operational_timer or StageTimer(operational_timings, clock=clock)
    updates = LiveUpdateChannel()
    if field_application is not None:
        field_application.attach_timing(timing)
    if isinstance(field_application, ContinuousGuardedApplication):
        field_application.attach_serialization(serialized)
        field_application.attach_updates(updates.publish)
    if preparation is not None:

        def announce_preparation(topic: str, review_case_id: str | None) -> None:
            updates.publish(topic, review_case_id)
            if topic == "library" and isinstance(
                field_application, ContinuousGuardedApplication
            ):
                field_application.wake()

        preparation.attach_updates(announce_preparation)
    operations = (
        None
        if operational_timings is None
        else OperationalStatus(
            snapshot=lambda: serialized(state_owner.operational_snapshot),
            timings=operational_timings,
            support=operational_support,
        )
    )
    overrides = {name: value for name, value in policy_libraries if value is not None}
    application = ReviewApplication(
        state_owner=state_owner,
        sessions=OwnerSessionService(
            session_store,
            verify_password,
            session_digest,
            clock,
            session_lifetime_seconds,
        ),
        cases=InspectCurrentReviewCase(case_store, cover_considerations),
        decisions=RecordHumanDecision(decision_store, clock, timing),
        browse=BrowseReviewLibrary(library_store),
        policy=PolicyNotebook(
            policy_store,
            tuple(name for name, _ in policy_libraries),
            overrides,
        ),
        automatic_safety=AutomaticSafetyControls(policy_store),
        clock=clock,
        graceful_shutdown_seconds=graceful_shutdown_seconds,
        close_state_owner=close_state_owner,
        preparation=preparation,
        field_application=field_application,
        operations=operations,
        gate=gate,
        updates=updates,
        activity_projection=activity_projection,
        close_activity_projection=close_activity_projection,
        browse_observation=browse_observation,
        close_browse=close_browse,
        report_browse=None
        if report_store is None
        else BrowseReviewLibrary(report_store),
        cover_considerations=cover_considerations,
        settings=settings,
        reviewer_access=reviewer_access,
        settings_activation=settings_activation,
        first_boot=first_boot,
        acknowledge_first_boot=acknowledge_first_boot,
    )
    return application


__all__ = [
    "AttentionView",
    "AutomaticSafetyOperations",
    "BrowserStateOwner",
    "CaseDetailView",
    "LibraryView",
    "PreparationView",
    "PolicyOperations",
    "ReviewApplication",
    "ReviewPolicyStore",
    "ReviewApplicationError",
    "compose_review_application",
    "prepare_cover_consideration",
]
