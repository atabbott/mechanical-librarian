"""Composed authenticated-browser operations over one serialized state owner."""

from __future__ import annotations

import time
from _thread import RLock
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from threading import Condition
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeVar, cast

from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationError,
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.cover_taste_build import (
    CoverTasteBuildService,
    CoverTasteRetryError,
)
from mechanical_librarian.application.failure_diagnostics import (
    emit_owner_events,
    owner_link_event,
)
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.application.identifier_import import (
    IMPORT_SCHEMA,
    WITHDRAWAL_SCHEMA,
    IdentifierImportRecord,
    IdentifierImportRefused,
    IdentifierImportService,
    IdentifierImportStore,
    emit_identifier_import,
)
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
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.operation_admission import (
    AdmissionClosedError,
    OperationAdmission,
    WorkClass,
)
from mechanical_librarian.application.operations import (
    OperationalStatus,
    StageTimer,
    SupportExporter,
    TimingRepository,
)
from mechanical_librarian.application.owner_search import (
    OwnerSearchRefused,
    OwnerSearchRequest,
    OwnerSearchService,
)
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.application.review_covers import (
    CoverBrowseStore,
    cover_page,
    replaced_cover_preview,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
    ReviewPreparationSupervisor,
    is_first_boot_scan_id,
)
from mechanical_librarian.application.review_web import (
    BrowseFetchingChanged,
    BrowseObservationOwner,
    BrowseReviewLibrary,
    CurrentReviewCaseStore,
    HumanDecisionStore,
    InspectCurrentReviewCase,
    LibraryLabels,
    OwnerPasswordChange,
    OwnerSessionService,
    ProviderRecoverySchedule,
    RecordHumanDecision,
    ReviewLibraryStore,
    ReviewSessionStore,
    ReviewWebError,
    SessionTokens,
    ValidatedDocumentCache,
    owner_link_view,
)
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.application.write_plan import PlanFormat, ProducedStream
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)
from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
from mechanical_librarian.domain.cover_taste import (
    TasteEditAction,
    TasteEditError,
    TasteSection,
)
from mechanical_librarian.domain.description_advice import DescriptionAdviceBinding
from mechanical_librarian.domain.field_effect import SUPPORTED_DESTINATIONS
from mechanical_librarian.domain.library_visit import ListingPositions
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.owner_link import OwnerLink, OwnerLinkTarget
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    OwnerSettingsError,
    SecretWrite,
    SettingsView,
)
from mechanical_librarian.domain.preparation_presentation import (
    PreparationPresentation,
    PreparationPresentationError,
    PreparationTrigger,
)
from mechanical_librarian.domain.proposal_set import work_route_cover_offered
from mechanical_librarian.domain.provider_recovery import MAX_TIMESTAMP
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

if TYPE_CHECKING:
    from mechanical_librarian.application.cover_consideration import (
        _ObservationRequest,
    )


ReviewPreparationSchemaVersion = Literal["review-preparation-v2"]
REVIEW_PREPARATION_SCHEMA: Final[ReviewPreparationSchemaVersion] = (
    "review-preparation-v2"
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


class BrowserStateOwner(IdentifierImportStore, Protocol):
    """Composition-only lifecycle and status capabilities of the SQLite owner.

    It is also the identifiers file import store (#1478).
    """

    def close(self) -> None: ...

    def operational_snapshot(self) -> dict[str, object]: ...

    def field_effect_capability_suspension(
        self, connection: str, destination: str
    ) -> str | None: ...

    def preparation_presentation(self, now: int) -> PreparationPresentation | None: ...

    def dismiss_preparation_presentation(
        self, run_id: str, dismissed_at: int
    ) -> None: ...

    def record_library_visit(
        self, listing: ListingPositions | None, now: int
    ) -> bool: ...

    # The owner-link store (#1477, S16): a replay read and the two writes,
    # each its own BEGIN IMMEDIATE transaction, and the link view's read.
    def owner_link_replay(self, request_id: str) -> dict[str, object] | None: ...

    def choose_owner_link(
        self,
        connection: str,
        library: str,
        item: str,
        *,
        medium: str,
        expected_current: str | None,
        target: OwnerLinkTarget,
        actor: str,
        occurred_at: int,
        request_id: str,
        request_sha256: str,
    ) -> dict[str, object]: ...

    def withdraw_owner_link(
        self,
        connection: str,
        library: str,
        item: str,
        *,
        medium: str,
        expected_current: str,
        actor: str,
        occurred_at: int,
        request_id: str,
        request_sha256: str,
    ) -> dict[str, object]: ...

    def owner_link_view_inputs(
        self, connection: str, library: str, item: str
    ) -> tuple[OwnerLink, OwnerLinkTarget, dict[str, object] | None, bool] | None: ...


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
    audiobook_review_scope: str | None
    # How the matched recording was found when its title differs (#1723).
    recording_admission_basis: dict[str, object] | None = None
    # The case waits for its Book matching check (#1355, S10).
    checking: bool = False
    # That check gave up; saving again, or a restart, retries it (#1355).
    check_given_up: bool = False

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
            cast("str | None", value["audiobook_review_scope"]),
            None
            if value["recording_admission_basis"] is None
            else dict(cast("dict[str, object]", value["recording_admission_basis"])),
            value.get("checking") is True,
            value.get("check_given_up") is True,
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
            "audiobook_review_scope": self.audiobook_review_scope,
            "recording_admission_basis": self.recording_admission_basis,
            "checking": self.checking,
            "check_given_up": self.check_given_up,
        }


@dataclass(frozen=True, slots=True)
class LibraryView:
    counts: dict[str, int]
    items: tuple[dict[str, object], ...]
    lens: str
    listing: dict[str, int]
    next_cursor: str | None
    page: int
    previous_cursor: str | None
    query: str | None
    schema_version: str

    @classmethod
    def from_data(cls, value: dict[str, object]) -> "LibraryView":
        counts = value["counts"]
        items = value["items"]
        listing = value["listing"]
        assert isinstance(counts, dict)
        assert isinstance(items, list)
        assert isinstance(listing, dict)
        return cls(
            {str(key): int(count) for key, count in counts.items()},
            tuple(dict(item) for item in items),
            str(value["lens"]),
            {str(key): int(position) for key, position in listing.items()},
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
            "listing": dict(self.listing),
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
    schema_version: str = REVIEW_PREPARATION_SCHEMA
    recovery_required: bool = False
    provider_throttling: dict[str, object] | None = None

    def to_data(self) -> dict[str, object]:
        return {
            "libraries": [dict(item) for item in self.libraries],
            "paused": self.paused,
            "active": self.active,
            "actionable": self.actionable,
            "recovery_required": self.recovery_required,
            "first_boot": self.first_boot,
            "first_boot_complete": self.first_boot_complete,
            "first_boot_works": self.first_boot_works,
            "connections": [dict(item) for item in self.connections],
            "presentation": None
            if self.presentation is None
            else dict(self.presentation),
            "provider_throttling": self.provider_throttling,
            "schema_version": self.schema_version,
        }


_THROTTLE_REASONS = frozenset({"rate_limited", "burst_throttled", "quota_exhausted"})
_KEY_REASONS = frozenset({"authentication", "authorization"})
# Reported state, in the precedence one library and source takes: a check in
# flight first, then a scheduled retry, then a wait on the owner, then nothing.
_THROTTLE_STATES = ("checking", "waiting", "dormant", "exhausted")
# A source whose lookups have waited this long without recovering is raised as
# a notice to the owner even though automatic retries still run.
_PROLONGED_WAIT = 86_400


def provider_throttling_view(
    rows: Iterable[Mapping[str, object]],
    providers: Mapping[str, bool],
    generation: int,
    retry: Literal["available", "managed_by_file", "unavailable"],
    answered: Iterable[tuple[str, str]] = (),
) -> dict[str, object]:
    """Total deferred provider lookups per library and source, value-free.

    ``providers`` maps each configured provider instance to whether it holds an
    API key; it is read only to choose a remedy, and no instance name leaves.
    ``notice_at`` is when the entry's longest current wait reaches a day; the
    browser compares it with its own clock, so no clock is read here.
    ``untried`` is true while a member due an automatic retry, or in one, has
    no outcome in this generation: its connection has not been tried since
    Retry now, so another member's reason is not yet the entry's outcome
    (#1494). A dormant or exhausted member is not waited for.
    ``answered`` names each library and source a provider answered in this
    generation, so Retry now's outcome claims answers only where there were
    some: a source also stops waiting when its lookups went stale (#1494).
    """
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in rows:
        if cast("int", row["deferred"]) > 0 or row["reason"] in _THROTTLE_REASONS:
            key = (cast("str", row["scope"]), cast("str", row["adapter"]))
            groups.setdefault(key, []).append(row)
    entries: list[dict[str, object]] = []
    for (library, adapter), members in sorted(groups.items())[:256]:
        reasons = sorted(
            {cast("str", item["reason"]) for item in members if item["reason"]}
        )
        states = {
            "checking" if item["state"] == "fanout" else item["state"]
            for item in members
        }
        due = [
            cast("int", item["next_eligible_at"])
            for item in members
            if item["state"] == "waiting"
        ]
        started = [
            cast("int", item["waiting_since"])
            for item in members
            if item["waiting_since"] is not None
        ]
        remedy = (
            "add_api_key"
            if adapter == "google_books"
            and _THROTTLE_REASONS.intersection(reasons)
            and all(
                providers.get(cast("str", item["connection"])) is False
                for item in members
            )
            else "check_api_key"
            if adapter in {"google_books", "hardcover"}
            and _KEY_REASONS.intersection(reasons)
            else None
        )
        entries.append(
            {
                "library": library,
                "adapter": adapter,
                "state": next(state for state in _THROTTLE_STATES if state in states),
                "reasons": reasons,
                "deferred": sum(cast("int", item["deferred"]) for item in members),
                "next_retry_at": min(due) if due else None,
                "remedy": remedy,
                "notice_at": min(min(started) + _PROLONGED_WAIT, MAX_TIMESTAMP)
                if started
                else None,
                "untried": any(
                    item["reason"] is None
                    and item["state"] in {"waiting", "checking", "fanout"}
                    for item in members
                ),
            }
        )
    return {
        "generation": generation,
        "retry": retry,
        "entries": entries,
        "answered": [
            {"library": library, "adapter": adapter} for library, adapter in answered
        ],
    }


class PolicyOperations:
    """Serialized named policy operations; transport never receives its store."""

    def __init__(
        self,
        notebook: PolicyNotebook,
        gate: SerializedGate,
        wake_field_application: Callable[[], None] = lambda: None,
    ) -> None:
        self._notebook = notebook
        self._gate = gate
        self._wake_field_application = wake_field_application

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
        state = self._gate(lambda: self._notebook.confirm_automatic(updated_at))
        # Confirming decides the works already prepared and makes their
        # decisions plannable, so the applier looks again now (#1573).
        self._wake_field_application()
        return state

    def enter_preview(self, updated_at: object) -> dict[str, object]:
        return self._gate(lambda: self._notebook.enter_preview(updated_at))

    def preview_leave(self) -> dict[str, object]:
        return self._gate(self._notebook.preview_leave)

    def shadow_sample(self, limit: object = 20) -> dict[str, object]:
        return self._gate(lambda: self._notebook.shadow_sample(limit))

    def activate(
        self,
        policy_id: object,
        expected_active_policy_id: object,
        activated_at: object,
        *,
        expected_active_generation: object,
    ) -> PolicyActivation:
        return self._gate(
            lambda: self._notebook.activate(
                policy_id,
                expected_active_policy_id,
                activated_at,
                expected_active_generation=expected_active_generation,
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
        updates: LiveUpdateChannel,
    ) -> None:
        self._controls = controls
        self._gate = gate
        self._updates = updates

    def state(self) -> AutomaticSafetyState:
        return self._gate(self._controls.state)

    def pause(self, paused: object, changed_at: object) -> None:
        self._gate(
            lambda: self._controls.pause(cast(bool, paused), cast(int, changed_at))
        )
        if paused is False:
            self._updates.publish("preparation")

    def recover(self, changed_at: object, category: object = None) -> None:
        self._gate(
            lambda: self._controls.recover(
                cast(int, changed_at), cast("str | None", category)
            )
        )
        if category in (None, "title_layout"):
            self._updates.publish("preparation")

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
        permission_projection: ContinuousGuardedApplication | None = None,
        close_activity_projection: Callable[[], None] | None = None,
        browse_observation: BrowseObservationOwner | None = None,
        admission: OperationAdmission | None = None,
        operation_lock: RLock | None = None,
        cover_considerations: CoverConsiderationService | None = None,
        export_projection: ContinuousGuardedApplication | None = None,
        cover_store: CoverBrowseStore | None = None,
        settings: OwnerSettingsOperations | None = None,
        reviewer_access: ReviewerAccessOperations | None = None,
        cover_taste: CoverTasteBuildService | None = None,
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
        provider_throttling: Callable[[], dict[str, object]] | None = None,
        align_book_matching: Callable[[int], object] | None = None,
        owner_search: OwnerSearchService | None = None,
        identifier_import: IdentifierImportService | None = None,
    ) -> None:
        self._updates = updates
        self._state_owner = state_owner
        self._sessions = sessions
        self._cases = cases
        self._decisions = decisions
        self._browse = browse
        self._browse_observation = browse_observation
        self._admission = admission or OperationAdmission()
        self._browse_closed = False
        self._browse_lifetime = Condition()
        self._active_browses = 0
        self._gate = operation_lock or gate
        self._policy = PolicyOperations(
            policy, self.serialized, self._wake_continuous_application
        )
        self._automatic_safety = AutomaticSafetyOperations(
            automatic_safety, self.serialized, updates
        )
        self._clock = clock
        self._graceful_shutdown_seconds = graceful_shutdown_seconds
        self._close_state_owner = close_state_owner
        self._preparation = preparation
        self._field_application = field_application
        self._activity_projection = activity_projection
        self._permission_projection = permission_projection
        self._export_projection = export_projection
        self._close_activity_projection = close_activity_projection
        self._operations = operations
        self._cover_considerations = cover_considerations
        self._cover_store = cover_store
        self._settings = settings
        self._reviewer_access = reviewer_access
        if reviewer_access is not None:
            reviewer_access.attach_serialization(self.serialized)
            reviewer_access.attach_changes(self._reviewer_changed)
        self._cover_taste = cover_taste
        self._settings_activation = settings_activation
        self._first_boot = first_boot
        self._acknowledge_first_boot = acknowledge_first_boot
        self._provider_throttling = provider_throttling
        self._align_book_matching = align_book_matching
        self._owner_search = owner_search
        self._identifier_import = identifier_import

    @property
    def owner_search(self) -> OwnerSearchService | None:
        """This generation's owner search service (#1477), if composed."""
        return self._owner_search

    def _require_owner_search(self) -> OwnerSearchService:
        if self._owner_search is None:
            raise ReviewApplicationError("unavailable")
        return self._owner_search

    def owner_link_view(
        self, connection: str, library: str, item: str
    ) -> dict[str, object]:
        """The selected work's Find this book offer and link (#1477, S29)."""
        subject = self._require_owner_search().subject(connection, library, item)
        inputs = self.serialized(
            lambda: self._state_owner.owner_link_view_inputs(connection, library, item)
        )
        paused = False
        next_retry_at: int | None = None
        if inputs is not None:
            if self._preparation is not None:
                paused = self._preparation.paused()
            provider = inputs[1].provider
            for entry in cast(
                "list[dict[str, object]]",
                (self._provider_throttling() if self._provider_throttling else {}).get(
                    "entries", []
                ),
            ):
                due = entry.get("next_retry_at")
                if (entry.get("library"), entry.get("adapter")) == (
                    library,
                    provider,
                ) and type(due) is int:
                    next_retry_at = due
        return owner_link_view(
            subject.to_offer(), inputs, paused=paused, next_retry_at=next_retry_at
        )

    def search_owner_link(
        self, session_token: str, request: OwnerSearchRequest
    ) -> dict[str, object]:
        """One owner search (S11). It runs outside the operation gate: its one
        request may wait up to ten seconds on the shared pacer and twenty on
        the source, and nothing it holds is a database write."""
        result = self._require_owner_search().search(session_token, request)
        return {**result.to_data(), "schema_version": "owner-search-v1"}

    def owner_link_preview(
        self, session_token: str, search_id: str, position: int
    ) -> bytes:
        return self._require_owner_search().preview(session_token, search_id, position)

    def choose_owner_link(
        self,
        session_token: str,
        actor: str,
        search_id: str,
        position: int,
        scope: str,
        request_id: str,
        request_sha256: str,
    ) -> dict[str, object]:
        """Record the chosen card as the work's link (S16 steps 3-8).

        The replay is read first, so a lost answer is given again after the
        search expired; the record is the retained card, never the browser's.
        """
        search = self._require_owner_search()
        resolved: list[OwnerLinkTarget] = []

        def write() -> dict[str, object]:
            replay = self._owner_link_replayed(request_id, request_sha256)
            if replay is not None:
                return replay
            choice = search.resolve_choice(session_token, search_id, position, scope)
            resolved.append(choice.target)
            return self._state_owner.choose_owner_link(
                choice.connection,
                choice.library,
                choice.item,
                medium=choice.medium,
                expected_current=choice.expected_current,
                target=choice.target,
                actor=actor,
                occurred_at=self._clock(),
                request_id=request_id,
                request_sha256=request_sha256,
            )

        return self._owner_link_written(
            write,
            "link",
            lambda: (
                (resolved[0].provider, resolved[0].scope.value)
                if resolved
                else (None, None)
            ),
        )

    def withdraw_owner_link(
        self,
        actor: str,
        connection: str,
        library: str,
        item: str,
        link_row_id: str,
        request_id: str,
        request_sha256: str,
    ) -> dict[str, object]:
        """Withdraw the work's current link, named by its row (S16)."""
        search = self._require_owner_search()

        def write() -> dict[str, object]:
            replay = self._owner_link_replayed(request_id, request_sha256)
            if replay is not None:
                return replay
            subject = search.resolve_withdraw(connection, library, item, link_row_id)
            return self._state_owner.withdraw_owner_link(
                connection,
                library,
                item,
                medium=subject.medium,
                expected_current=link_row_id,
                actor=actor,
                occurred_at=self._clock(),
                request_id=request_id,
                request_sha256=request_sha256,
            )

        return self._owner_link_written(write, "withdraw", lambda: (None, None))

    def _owner_link_replayed(
        self, request_id: str, request_sha256: str
    ) -> dict[str, object] | None:
        """S16 step 3: the stored answer, or `idempotency_conflict`."""
        replay = self._state_owner.owner_link_replay(request_id)
        if replay is not None and replay["request_sha256"] != request_sha256:
            raise OwnerSearchRefused("idempotency_conflict")
        return replay

    def _owner_link_written(
        self,
        write: Callable[[], dict[str, object]],
        attempted: str,
        target: Callable[[], tuple[str | None, str | None]],
    ) -> dict[str, object]:
        """Run one link write under the gate, then ask for the scan.

        After the commit the library's scan is asked for with `book_linked`,
        a replay included (Q-F): the request is coalesced, and a refusal is
        swallowed because the work's fresh mark makes the next scan prepare
        it (S20). Only the existing live topics are announced.
        """
        try:
            answer = self.serialized(write)
        except OwnerSearchRefused as refusal:
            adapter, scope = target()
            _emit_owner_link(adapter, scope, attempted, refusal.code)
            raise
        except Exception:
            adapter, scope = target()
            _emit_owner_link(adapter, scope, attempted, "failed")
            raise
        adapter, scope = target()
        _emit_owner_link(
            adapter,
            scope,
            cast(str, answer["action"]),
            "replayed" if answer["replayed"] else "recorded",
        )
        connection, library = (
            cast(str, answer["connection"]),
            cast(str, answer["library"]),
        )
        try:
            self._preparation_request(
                "request", connection, library, trigger=PreparationTrigger.BOOK_LINKED
            )
            scan_requested = True
        except Exception:
            scan_requested = False
        review_case_id = cast("str | None", answer["review_case_id"])
        self._updates.publish("library", review_case_id)
        self._updates.publish("activity")
        if review_case_id is not None:
            self._updates.publish("case", review_case_id)
        return {
            "action": answer["action"],
            "link_row_id": answer["link_row_id"],
            "scan_requested": scan_requested,
            "schema_version": "owner-link-change-v1",
        }

    def _require_identifier_import(self) -> IdentifierImportService:
        if self._identifier_import is None:
            raise ReviewApplicationError("unavailable")
        return self._identifier_import

    def preview_identifiers(
        self, session_token: str, connection: str, library: str, data: bytes
    ) -> dict[str, object]:
        """Decide an identifiers file against the library and hold it (#1478).

        Only the catalogue read takes the gate; the file is classified outside
        it, and nothing is written.
        """
        service = self._require_identifier_import()
        service.require_library(connection, library)
        try:
            read = self.serialized(
                lambda: service.store.identifier_candidates(connection, library)
            )
        except IdentifierImportRefused as refusal:
            emit_identifier_import("preview", refusal.code)
            raise
        return service.preview(session_token, connection, library, data, read)

    def identifier_preview_status(
        self, session_token: str, preview_id: str
    ) -> dict[str, object]:
        """After an uncertain confirm: imported, still open, or closed (S11)."""
        service = self._require_identifier_import()
        return self.serialized(
            lambda: service.preview_status(session_token, preview_id)
        )

    def identifier_import_status(self, import_id: str) -> dict[str, object]:
        """An import's live state for Withdraw this import, never cached."""
        service = self._require_identifier_import()
        return self.serialized(lambda: service.import_status(import_id))

    def import_identifiers(
        self,
        session_token: str,
        actor: str,
        preview_id: str,
        request_id: str,
        request_sha256: str,
    ) -> dict[str, object]:
        """Record the session's preview as one import (S9, S11).

        The replay is read first, so a lost answer is given again after the
        preview closed; the links are the held preview's, never the browser's.
        """
        service = self._require_identifier_import()
        rows: list[int] = []

        def write() -> tuple[IdentifierImportRecord, int, bool]:
            replay = service.replayed(request_id, request_sha256)
            if replay is not None:
                return replay, replay.linked, True
            preview = service.open_preview(session_token, preview_id)
            rows.append(preview.result.rows)
            recorded, replayed = service.store.import_identifiers(
                preview.connection,
                preview.library,
                preview_digest=preview.digest,
                catalogue_digest=preview.catalogue_digest,
                rows=preview.result.rows,
                counts=preview.result.counts,
                links=preview.result.will_link,
                actor=actor,
                occurred_at=self._clock(),
                request_id=request_id,
                request_sha256=request_sha256,
            )
            service.forget(session_token)
            return recorded, recorded.linked, replayed

        recorded, linked, _ = self._identifier_import_written(
            write, "import", lambda: rows[0] if rows else 0
        )
        return {
            "import_id": recorded.import_id,
            "linked": linked,
            "scan_requested": self._identifier_scan(recorded),
            "schema_version": IMPORT_SCHEMA,
        }

    def withdraw_identifier_import(
        self, actor: str, import_id: str, request_id: str, request_sha256: str
    ) -> dict[str, object]:
        """Withdraw each of the import's links still current (S11)."""
        service = self._require_identifier_import()
        recorded, withdrawn, _ = self._identifier_import_written(
            lambda: service.store.withdraw_identifier_import(
                import_id,
                actor=actor,
                occurred_at=self._clock(),
                request_id=request_id,
                request_sha256=request_sha256,
            ),
            "withdraw",
            lambda: 0,
        )
        return {
            "import_id": recorded.import_id,
            "withdrawn": withdrawn,
            "scan_requested": self._identifier_scan(recorded),
            "schema_version": WITHDRAWAL_SCHEMA,
        }

    def _identifier_import_written(
        self,
        write: Callable[[], tuple[IdentifierImportRecord, int, bool]],
        action: str,
        rows: Callable[[], int],
    ) -> tuple[IdentifierImportRecord, int, bool]:
        """Run one import write under the gate and emit its one event."""
        try:
            answer = self.serialized(write)
        except IdentifierImportRefused as refusal:
            emit_identifier_import(action, refusal.code, rows=rows())
            raise
        except Exception:
            emit_identifier_import(action, "failed", rows=rows())
            raise
        emit_identifier_import(
            action,
            "replayed" if answer[2] else "recorded",
            rows=rows(),
            linked=answer[1],
        )
        return answer

    def _identifier_scan(self, recorded: IdentifierImportRecord) -> bool:
        """Ask for the library's scan after an import write, a replay
        included, and announce it; a refused request is only reported."""
        try:
            self._preparation_request(
                "request",
                recorded.connection_name,
                recorded.library_name,
                trigger=PreparationTrigger.BOOK_LINKED,
            )
            scan_requested = True
        except Exception:
            scan_requested = False
        self._updates.publish("library")
        self._updates.publish("activity")
        return scan_requested

    def _reviewer_changed(self) -> None:
        # Other sessions still invalidate when a rescan request is refused.
        try:
            if self._preparation is not None:
                for connection, library, *_ in self._preparation.libraries:
                    self._preparation.request(connection, library)
        finally:
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

    def classify_request(self, work_class: WorkClass) -> AbstractContextManager[None]:
        """Carry request purpose to local operations without granting authority."""

        @contextmanager
        def classified() -> Iterator[None]:
            with (
                self._admission.classify(work_class),
                nullcontext()
                if self._activity_projection is None
                else self._activity_projection.classify_request(work_class),
                nullcontext()
                if self._permission_projection is None
                else self._permission_projection.classify_request(work_class),
                nullcontext()
                if self._export_projection is None
                else self._export_projection.classify_request(work_class),
            ):
                yield

        return classified()

    def serialized(self, call: Callable[[], _Result]) -> _Result:
        try:
            with self._admission.enter(), self._gate:
                return call()
        except AdmissionClosedError:
            raise ReviewApplicationError("unavailable") from None

    @property
    def settings_available(self) -> bool:
        return self._settings is not None

    @property
    def reviewer_access(self) -> ReviewerAccessOperations | None:
        return self._reviewer_access

    def taste_profile(self) -> dict[str, object]:
        """Covers → Taste profile: configured, builds and versions (#1372)."""
        if self._cover_taste is None:
            raise ReviewApplicationError("unavailable")
        return self._cover_taste.read()

    def retry_taste_build(self, build_id: str) -> None:
        if self._cover_taste is None:
            raise ReviewApplicationError("unavailable")
        try:
            self._cover_taste.retry(build_id)
        except CoverTasteRetryError:
            raise ReviewApplicationError("stale") from None
        self._updates.publish("library")

    def edit_taste_rule(
        self,
        version: int,
        action: TasteEditAction,
        number: int | None,
        section: TasteSection | None,
        text: str | None,
    ) -> None:
        """One owner edit of the taste profile (#1372 PR 2); a refusal is its
        closed code and writes nothing."""
        if self._cover_taste is None:
            raise ReviewApplicationError("unavailable")
        try:
            self._cover_taste.edit(version, action, number, section, text)
        except TasteEditError as error:
            raise ReviewApplicationError(error.code) from None
        self._updates.publish("library")

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
                            current_protection=preferences.current_protection,
                            prefer_edition=preferences.prefer_edition,
                        ),
                        WorkIdentityLevel(validated.matching.work_identity),
                    )
                    if self._cover_considerations is not None
                    else None
                )

                def commit() -> object:
                    return self.serialized(
                        lambda: settings.write(
                            validated,
                            revision,
                            secret_writes,
                            cover_update,
                            accepted_at=self._clock(),
                        )
                    )

                if self._settings_activation is not None:
                    return self._settings_activation(
                        validated, revision, secret_writes, commit
                    )
                return commit()

            return write()
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

    def retry_provider_recovery(self, expected_generation: int) -> object:
        """Raise the recovery generation by exactly one, as one Settings write."""
        view = cast("SettingsView", self.owner_settings())
        if view.authority == "file":
            raise ReviewApplicationError("settings_locked")
        current = view.document
        if current.provider_recovery_generation != expected_generation:
            raise ReviewApplicationError("provider_recovery_stale")
        document = OwnerSettingsDocument.model_validate(
            {
                **current.model_dump(),
                "revision": current.revision + 1,
                "provider_recovery_generation": expected_generation + 1,
            }
        )
        value = self.write_owner_settings(document, current.revision, ())
        self._updates.publish("preparation")
        return value

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
        """Open the already-started generation after no-fail publication.

        The configured Book matching level may have changed while no
        generation ran, so every case follows it before any owner write or
        scheduler claim is admitted (#1355, amendment 5).
        """
        if self._align_book_matching is not None:
            self._align_book_matching(self._clock())
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
        with self._browse_lifetime:
            self._browse_closed = True
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
        with self._browse_lifetime:
            self._browse_closed = True
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
        with self._browse_lifetime:
            browse_drained = self._browse_lifetime.wait_for(
                lambda: self._active_browses == 0,
                timeout=max(0.0, deadline - time.monotonic()),
            )
        if browse_drained and self._close_activity_projection is not None:
            try:
                self._close_activity_projection()
            except Exception as error:
                failures.append(error)
        if self._preparation is not None:
            try:
                if browse_drained:
                    self._preparation.stop(deadline)
                else:
                    self._preparation.wait_for_drain(deadline)
            except Exception as error:
                failures.append(error)
        if not browse_drained:
            failures.append(ReviewApplicationError("unavailable"))
        if browse_drained and self._reviewer_access is not None:
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

    # The six session operations below deliberately bypass ``serialized``:
    # the session store owns its dedicated database and lock (#912), so owner
    # sign-in and session checks never queue behind an operation that holds
    # the application gate while it waits on a scan's write transaction.

    def login(self, password: str, client: str) -> SessionTokens:
        return self._sessions.login(password, client)

    def authenticate(self, session_token: str, csrf_token: str | None = None) -> str:
        return self._sessions.authenticate(session_token, csrf_token)

    def issue_browser_csrf(self, session_token: str) -> str:
        return self._sessions.issue_browser_csrf(session_token)

    def logout(self, session_token: str, csrf_token: str) -> None:
        self._sessions.logout(session_token, csrf_token)
        # The session's search state and identifiers file preview go with it
        # (#1477 S13, #1478 S7).
        if self._owner_search is not None:
            self._owner_search.forget(session_token)
        if self._identifier_import is not None:
            self._identifier_import.forget(session_token)

    def logout_others(self, session_token: str, csrf_token: str) -> int:
        return self._sessions.logout_others(session_token, csrf_token)

    def change_owner_password(
        self,
        session_token: str,
        csrf_token: str,
        current_password: str,
        new_password: str,
        confirmation: str,
        client: str,
    ) -> SessionTokens:
        return self._sessions.change_password(
            session_token,
            csrf_token,
            current_password,
            new_password,
            confirmation,
            client,
        )

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

    def _library_labels(self) -> dict[tuple[str, str], str]:
        # Settings enforce globally unique stable library names. Read once for
        # each projection; a presentation rename never rebinds a target.
        return (
            {}
            if self._settings is None
            else {
                (item.connection, item.name): item.display_name or item.name
                for item in self._settings.read().document.libraries
            }
        )

    def _labelled_collection(
        self, value: dict[str, object], labels: dict[tuple[str, str], str]
    ) -> dict[str, object]:
        by_library = {library: label for (_, library), label in labels.items()}
        for item in cast(list[dict[str, object]], value["items"]):
            library = str(item["library"])
            item["library_display_name"] = by_library.get(library, library)
        return value

    def case_detail(self, review_case_id: str) -> CaseDetailView:
        def read() -> CaseDetailView:
            detail = self._cases.run(review_case_id)
            target = detail["target"]
            assert isinstance(target, dict)
            target["library_display_name"] = self._library_labels().get(
                (str(target["connection"]), str(target["library"])),
                str(target["library"]),
            )
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

    @contextmanager
    def _accepted_read(self) -> Iterator[None]:
        with self._browse_lifetime:
            if self._browse_closed:
                raise ReviewApplicationError("unavailable")
            self._active_browses += 1
        try:
            yield
        finally:
            with self._browse_lifetime:
                self._active_browses -= 1
                self._browse_lifetime.notify_all()

    def _browse_serialized(
        self,
        call: Callable[[], _Result],
        check_active: Callable[[], None] | None = None,
    ) -> _Result:
        with self._accepted_read():
            try:
                owner = self._browse_observation
                authority = None
                for attempt in range(2):
                    if check_active is not None:
                        check_active()
                    observation = (
                        None if owner is None else owner.capture_browse_observation()
                    )
                    if attempt == 0:
                        authority = None if observation is None else observation[0]
                    elif observation is None or observation[0] != authority:
                        raise ReviewWebError("unavailable")
                    try:
                        with (
                            self._admission.enter(check_active=check_active),
                            self._gate,
                        ):
                            if check_active is not None:
                                check_active()
                            with (
                                nullcontext()
                                if owner is None
                                else owner.install_browse_observation(observation)
                            ):
                                result = call()
                    except AdmissionClosedError:
                        raise ReviewApplicationError("unavailable") from None
                    if owner is not None:
                        try:
                            owner.recheck_browse_observation(observation)
                        except BrowseFetchingChanged:
                            if attempt == 0:
                                continue
                            raise
                    return result
                raise ReviewWebError("unavailable")
            except MetadataReportError:
                raise ReviewWebError("unavailable") from None

    def library(
        self,
        *,
        lens: str,
        search: str | None,
        limit: int,
        cursor: str | None,
        check_active: Callable[[], None] | None = None,
    ) -> LibraryView:
        def read() -> LibraryView:
            labels = self._library_labels()
            snapshot: LibraryLabels = tuple(
                (connection, library, label)
                for (connection, library), label in labels.items()
            )
            return LibraryView.from_data(
                self._labelled_collection(
                    self._browse.library(
                        lens=lens,
                        search=search,
                        limit=limit,
                        cursor=cursor,
                        library_labels=snapshot,
                        now=self._clock(),
                        find_offer=None
                        if self._owner_search is None
                        else self._owner_search.find_offer(),
                    ),
                    labels,
                )
            )

        return self._browse_serialized(read, check_active)

    def record_library_visit(self, listing: ListingPositions | None) -> None:
        """Record one use of Library by the owner (#1351, ADR-0103).

        `listing` is how far a New lens the browser showed had got, or None
        for any other use. Other tabs are told to read Library again only
        when what counts as new changed: the first mark changes nothing.
        """
        try:
            moved = self.serialized(
                lambda: self._state_owner.record_library_visit(listing, self._clock())
            )
        except MetadataReportError:
            raise ReviewApplicationError("persistence") from None
        if moved:
            self._updates.publish("library")

    def library_progress(
        self, *, check_active: Callable[[], None] | None = None
    ) -> dict[str, object]:
        def read() -> dict[str, object]:
            snapshot: LibraryLabels = tuple(
                (connection, library, label)
                for (connection, library), label in self._library_labels().items()
            )
            return self._browse.progress(snapshot, self._clock())

        return self._browse_serialized(read, check_active)

    def attention(
        self,
        *,
        search: str | None,
        limit: int,
        cursor: str | None,
        check_active: Callable[[], None] | None = None,
    ) -> AttentionView:
        def read() -> AttentionView:
            labels = self._library_labels()
            snapshot: LibraryLabels = tuple(
                (connection, library, label)
                for (connection, library), label in labels.items()
            )
            return AttentionView.from_data(
                self._labelled_collection(
                    self._browse.attention(
                        search=search,
                        limit=limit,
                        cursor=cursor,
                        library_labels=snapshot,
                    ),
                    labels,
                )
            )

        return self._browse_serialized(read, check_active)

    def report_detail(
        self, connection: str, library: str, item: str
    ) -> dict[str, object]:
        """Read one item's recorded evidence where no review case exists."""

        def read() -> dict[str, object]:
            detail = self._browse.report_detail(connection, library, item)
            detail["library_display_name"] = self._library_labels().get(
                (connection, library), library
            )
            return detail

        return self.serialized(read)

    def covers(
        self,
        *,
        view: str,
        cursor: str | None,
        cover: str | None = None,
        check_active: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        """Read one page of a Covers tab, or one book under it (#1633)."""
        store = self._cover_store
        if store is None:
            raise ReviewWebError("unavailable")
        return self._browse_serialized(
            lambda: cover_page(store, view, cursor, cover), check_active
        )

    def replaced_cover(self, plan: str, preview: str) -> bytes:
        """Read the preview of an original a listed switch replaced."""
        store = self._cover_store
        cover_considerations = self._cover_considerations
        if store is None or cover_considerations is None:
            raise ReviewWebError("unavailable")

        def read() -> bytes:
            digest = replaced_cover_preview(store, plan, preview)
            try:
                return cover_considerations.retained_preview(digest)
            except CoverConsiderationError as error:
                raise ReviewWebError("unavailable") from error

        return self.serialized(read)

    def library_cover(
        self, connection: str, library: str, item: str, preview: str
    ) -> bytes:
        """Read the library cover preview a work's current report retains."""
        if self._cover_considerations is None:
            raise ReviewWebError("unavailable")
        cover_considerations = self._cover_considerations

        def read() -> bytes:
            digest = self._browse.library_cover(connection, library, item, preview)
            try:
                return cover_considerations.retained_preview(digest)
            except CoverConsiderationError as error:
                raise ReviewWebError("unavailable") from error

        return self.serialized(read)

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
        cover_confirmation: dict[str, object] | None = None,
        offered_alternatives_digest: str | None = None,
    ) -> dict[str, object]:
        if destination == "cover" and (
            self._cover_considerations is None or consideration_id is None
        ):
            raise ReviewApplicationError("cover_consideration_required")

        def record() -> dict[str, object]:
            if destination == "cover" and self._cover_considerations is not None:
                self._cover_considerations.refresh_authority()
            result = self._decisions.run(
                review_case_id,
                proposal_id=proposal_id,
                target_fingerprint=target_fingerprint,
                destination=destination,
                actor=actor,
                outcome=outcome,
                selected_value=selected_value,
                idempotency_key=idempotency_key,
                consideration_id=consideration_id,
                cover_confirmation=cover_confirmation,
                offered_alternatives_digest=offered_alternatives_digest,
            )
            return {
                **result,
                "confirmation": self._decision_confirmation(review_case_id),
            }

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
        # A closed channel publishes nothing, so the list can be shorter or
        # empty; the write is committed either way.
        return {
            **result,
            "invalidation_sequences": self._announce_decision(review_case_id),
        }

    def _announce_decision(self, review_case_id: str) -> list[int]:
        """Announce a recorded decision; the positions actually published."""
        published = (
            self._updates.publish("case", review_case_id),
            self._updates.publish("library", review_case_id),
            self._updates.publish("activity"),
        )
        return [sequence for sequence in published if sequence is not None]

    def _decision_confirmation(self, review_case_id: str) -> dict[str, object]:
        """Read committed authority without turning a read failure into write failure."""
        try:
            detail = self.case_detail(review_case_id)
        except Exception:
            return {"status": "unavailable"}
        return {"status": "confirmed", "case": detail.to_data()}

    def retry_description_advice(self, review_case_id: str) -> dict[str, object]:
        connection, library = self.serialized(
            lambda: self._decisions.retry_description(review_case_id)
        )
        # The retry is committed and waits in persistence until a scan of its
        # library runs, including while preparation is paused (configuration.md,
        # "Description advice"). Waking preparation only shortens that wait, so
        # its refusal never reports the saved retry as failed (ADR-0091).
        try:
            if self._preparation is not None:
                self._preparation.request(connection, library)
        except ReviewPipelineError:
            pass
        finally:
            self._updates.publish("case", review_case_id)
        return {"queued": True}

    def select_description_advice(
        self,
        review_case_id: str,
        candidate_id: str,
        binding: DescriptionAdviceBinding,
        idempotency_key: str,
    ) -> dict[str, object]:
        def record() -> dict[str, object]:
            result = self._decisions.select_description(
                review_case_id, candidate_id, binding, idempotency_key
            )
            return {
                **result,
                "confirmation": self._decision_confirmation(review_case_id),
            }

        result = self.serialized(record)
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.wake()
        return {
            **result,
            "invalidation_sequences": self._announce_decision(review_case_id),
        }

    def cover_image(
        self, review_case_id: str, consideration_id: str, reference: str
    ) -> tuple[bytes, bool]:
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

        with self._accepted_read():
            if self._activity_projection is not None:
                return project(self._activity_projection)
            return project(self._continuous_application())

    def activity_permission_readiness(self) -> dict[str, object]:
        with self._accepted_read():
            return self._permission_reader().activity_permission_readiness()

    def activity_write_plan_file(self, format: PlanFormat) -> ProducedStream:
        """Start the write plan file on its own reader (#1336).

        A file can take minutes, so neither the permission reader nor the
        Activity history reader waits for it. It counts as an accepted read
        until its producer has ended its read, so shutdown waits for its
        reader; the producer ends by itself once the application is closing
        or nobody has read for a minute, whatever the response does.
        """
        with self._browse_lifetime:
            if self._browse_closed:
                raise ReviewApplicationError("unavailable")
            self._active_browses += 1

        def release() -> None:
            with self._browse_lifetime:
                self._active_browses -= 1
                self._browse_lifetime.notify_all()

        try:
            labels = {
                library: label for (_, library), label in self._library_labels().items()
            }
            # Never the permission reader, which can be the writer: a file
            # keeps its reader for minutes.
            reader = self._export_projection
            if reader is None:
                raise ReviewApplicationError("unavailable")
            stream = reader.activity_write_plan_file(
                format, lambda: self._browse_closed, labels
            )
        except BaseException:
            release()
            raise
        stream.when_closed(release)
        return stream

    def activity_consent(self) -> dict[str, object]:
        # The permission reader borrows no Activity facts, so neither the
        # history catch-up nor a history request can delay this read (#1236).
        with self._accepted_read():
            return self._permission_reader().activity_consent()

    def activity_transitions(
        self, event_id: str, after_sequence: int | None = None
    ) -> dict[str, object]:
        with self._accepted_read():
            if self._activity_projection is not None:
                return self._activity_projection.activity_transitions(
                    event_id, after_sequence
                )
            return self._continuous_application().activity_transitions(
                event_id, after_sequence
            )

    def enable_activity(
        self, grants: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> dict[str, object]:
        application = self._continuous_application()
        result = application.enable(grants)
        self._updates.publish("activity")
        return result

    def revoke_activity(self, connection: str, library: str) -> dict[str, object]:
        application = self._continuous_application()
        result = application.revoke(connection, library)
        self._updates.publish("activity")
        return result

    def return_to_automatic(self, review_case_id: str, destination: str) -> None:
        if destination not in SUPPORTED_DESTINATIONS:
            raise KeyError("not_found")
        application = self._continuous_application()
        # Resume releases only a hold the case shows holding a decision, as its
        # control does: with nothing to hold, a withdrawal would remove the
        # released row and let an older copy of the hold return (ADR-0085).
        if not self.serialized(lambda: self._shows_hold(review_case_id, destination)):
            raise GuardedApplicationError("manual_hold")
        application.return_to_automatic(review_case_id, destination)
        self._updates.publish("case", review_case_id)
        self._updates.publish("activity")

    def _shows_hold(self, review_case_id: str, destination: str) -> bool:
        try:
            fields = self._cases.run(review_case_id)["fields"]
        except ReviewWebError:
            # A case that cannot be read shows nothing, so it releases nothing.
            return False
        return any(
            isinstance(field, dict)
            and field["destination"] == destination
            and field["manual_hold"] is True
            for field in cast("list[object]", fields)
        )

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
        application = self._field_application
        if operation == "status":
            return self.serialized(lambda: application.status(plan_id).to_data())
        if operation == "apply":
            return application.apply(plan_id).to_data()
        return application.reconcile(plan_id).to_data()

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
        throttling = (
            None if self._provider_throttling is None else self._provider_throttling()
        )
        if self._preparation is None:
            return PreparationView(
                (),
                False,
                False,
                False,
                presentation=None if presentation is None else presentation.to_data(),
                provider_throttling=throttling,
            )
        progress_values, paused = self._preparation.status()
        if len(progress_values) != len(self._preparation.libraries):
            raise ReviewApplicationError("persistence")
        first_boot = self._first_boot and all(
            progress is None
            or is_first_boot_scan_id(progress.scan_id, connection, library)
            for (connection, library, _library_id), progress in zip(
                self._preparation.libraries, progress_values, strict=True
            )
        )
        conditions = self._preparation.connection_conditions()
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
                "not_matched": 0 if progress is None else progress.not_matched,
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
        recovery_required = self._preparation.recovery_required
        # The run's figures are the current state of its works, summed from the
        # rows above that it enrolled: one read, so the run line and those rows
        # agree exactly, and a library outside the run is the only way their
        # totals differ. A read that saw another run than the presentation
        # leaves the figures unknown rather than summing none (#1366).
        in_run = tuple(
            progress
            for progress in progress_values
            if progress is not None and progress.in_run
        )
        replaced = presentation is not None and any(
            progress is not None and progress.run_id != presentation.run_id
            for progress in progress_values
        )
        now = (
            None
            if replaced
            else {
                "ready": sum(progress.ready for progress in in_run),
                "recorded": sum(progress.recorded for progress in in_run),
                "not_matched": sum(progress.not_matched for progress in in_run),
                "failed": sum(progress.blocked for progress in in_run),
                "in_progress": sum(
                    max(progress.total - progress.checked, 0) for progress in in_run
                ),
                "scan_ids": [progress.scan_id for progress in in_run],
            }
        )
        return PreparationView(
            libraries,
            paused,
            not recovery_required
            and any(
                item["state"] in {"queued", "discovering", "processing"}
                for item in libraries
            ),
            recovery_required
            or any(item["state"] == "completed_with_failures" for item in libraries),
            connections,
            first_boot,
            first_boot_complete,
            sum(cast("int", item["total"]) for item in libraries)
            if first_boot_complete
            else None,
            None if presentation is None else {**presentation.to_data(), "now": now},
            recovery_required=recovery_required,
            provider_throttling=throttling,
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
        self,
        action: Literal["request", "retry"],
        connection: str,
        library: str,
        trigger: PreparationTrigger = PreparationTrigger.MANUAL_SCAN,
    ) -> dict[str, object]:
        if self._preparation is None:
            raise ReviewApplicationError("unavailable")
        selected = getattr(self._preparation, action)
        progress = (
            selected(connection, library)
            if action == "retry"
            else selected(connection, library, trigger=trigger)
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
            "not_matched": progress.not_matched,
            "rescan_requested": progress.rescan_requested,
            "stages": [
                {"stage": stage, "count": count} for stage, count in progress.stages
            ],
        }

    def _set_preparation_paused(self, paused: bool) -> PreparationView:
        if self._preparation is None:
            raise ReviewApplicationError("unavailable")
        self._preparation.set_paused(paused)
        return self.preparation()

    def _permission_reader(self) -> ContinuousGuardedApplication:
        application = self._permission_projection
        return self._continuous_application() if application is None else application

    def _wake_continuous_application(self) -> None:
        if isinstance(self._field_application, ContinuousGuardedApplication):
            self._field_application.wake()

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
        bound_report = MetadataReport.from_json(report_document)
        if (
            bound_report.audio is not None
            and not admitted_audiobook_identity(bound_report)
            and not work_route_cover_offered(bound_report)
        ):
            raise ReviewApplicationError("unavailable")
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
    client_digest: Callable[[str], str],
    owner_password_change: OwnerPasswordChange | None = None,
    close_state_owner: bool = False,
    preparation: ReviewPreparationSupervisor | None = None,
    policy_libraries: tuple[tuple[str, dict[str, object] | None], ...] = (),
    field_application: GuardedFieldApplication
    | ContinuousGuardedApplication
    | None = None,
    activity_projection: ContinuousGuardedApplication | None = None,
    permission_projection: ContinuousGuardedApplication | None = None,
    export_projection: ContinuousGuardedApplication | None = None,
    close_activity_projection: Callable[[], None] | None = None,
    browse_observation: BrowseObservationOwner | None = None,
    admission: OperationAdmission | None = None,
    operation_lock: RLock | None = None,
    operational_timings: TimingRepository | None = None,
    operational_support: SupportExporter | None = None,
    operational_timer: StageTimer | None = None,
    cover_considerations: CoverConsiderationService | None = None,
    settings: OwnerSettingsOperations | None = None,
    reviewer_access: ReviewerAccessOperations | None = None,
    cover_taste: CoverTasteBuildService | None = None,
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
    validated_documents: ValidatedDocumentCache | None = None,
    provider_throttling: Callable[[], dict[str, object]] | None = None,
    provider_recovery: ProviderRecoverySchedule | None = None,
    align_book_matching: Callable[[int], object] | None = None,
    owner_search: OwnerSearchService | None = None,
) -> ReviewApplication:
    """Compose every operation before the HTTP adapter is created."""
    gate = operation_lock or RLock()
    admission = admission or OperationAdmission()

    def serialized(call: Callable[[], _Result]) -> _Result:
        with admission.enter(), gate:
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
            runtime_incidents=lambda: (
                () if preparation is None else preparation.recovery_incidents
            ),
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
            client_digest=client_digest,
            password_change=owner_password_change,
        ),
        cases=InspectCurrentReviewCase(case_store, cover_considerations),
        decisions=RecordHumanDecision(decision_store, clock, timing),
        browse=BrowseReviewLibrary(
            library_store,
            ValidatedDocumentCache()
            if validated_documents is None
            else validated_documents,
            provider_recovery,
        ),
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
        permission_projection=permission_projection,
        export_projection=export_projection,
        close_activity_projection=close_activity_projection,
        browse_observation=browse_observation,
        admission=admission,
        operation_lock=gate,
        cover_considerations=cover_considerations,
        cover_store=library_store,
        settings=settings,
        reviewer_access=reviewer_access,
        cover_taste=cover_taste,
        settings_activation=settings_activation,
        first_boot=first_boot,
        acknowledge_first_boot=acknowledge_first_boot,
        provider_throttling=provider_throttling,
        align_book_matching=align_book_matching,
        owner_search=owner_search,
        identifier_import=None
        if owner_search is None
        else IdentifierImportService(
            store=state_owner,
            library_configured=owner_search.library_configured,
            clock=clock,
        ),
    )
    return application


def _emit_owner_link(
    adapter: str | None, scope: str | None, action: str, outcome: str
) -> None:
    emit_owner_events(
        owner_link_event, adapter=adapter, scope=scope, action=action, outcome=outcome
    )


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
    "provider_throttling_view",
]
