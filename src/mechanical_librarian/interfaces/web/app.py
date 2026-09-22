"""FastAPI adapter for exact-current review and one human decision command."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import stat
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal, Self, TypeAlias, TypedDict, TypeVar, cast
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.security import APIKeyCookie
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
    model_validator,
)
from starlette.concurrency import run_in_threadpool
from starlette.types import Message, Receive, Scope, Send

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.live_updates import (
    LiveUpdateCapacityError,
    LiveUpdateSubscription,
)
from mechanical_librarian.application.metadata_policy import (
    MetadataPolicyApplicationError,
    PolicyTransition,
)
from mechanical_librarian.application.operations import OperationsError
from mechanical_librarian.application.review_application import (
    ReviewApplication,
    ReviewApplicationError,
)
from mechanical_librarian.application.review_web import ReviewWebError
from mechanical_librarian.application.reviewer_access import (
    CodexDescriptionDisclosure,
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
    ReviewerAccessRepositoryError,
    ReviewerAccessView,
)
from mechanical_librarian.application.reviewer_contracts import (
    CodexDeviceAuthState,
    DeviceAuthFailure,
    DeviceAuthStatus,
)
from mechanical_librarian.domain.audio_health import MAX_HEALTH_OBSERVATIONS
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
)
from mechanical_librarian.domain.field_effect import (
    SUPPORTED_DESTINATIONS,
    FieldEffectError,
    valid_activity_transition_id,
)
from mechanical_librarian.domain.metadata_policy import MetadataPolicyError
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    SecretWrite,
    SettingsView,
)
from mechanical_librarian.domain.reviewer_access import (
    DescriptionModelMetadata,
    OpenRouterModelId,
)
from mechanical_librarian.interfaces.web import live_stream, review_frontend
from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetError,
    ReactAssetLoader,
    ReactAssetRecovery,
)

_CASE_ID = re.compile(r"^review_case_[0-9a-f]{64}$")
_COOKIE_NAME = "ml_session"
_ShellResponse: TypeAlias = HTMLResponse | RedirectResponse

# The rule these seven were chosen by:
#
#   409 — the current recorded state refuses this effect; a decision changes it.
#   503 — the machinery could not answer; time or repair changes it.
#
# A suspended capability, a revoked authority and a withdrawn consent are
# permanent refusals an owner resolves and a retry never does, which is what
# RFC 9110 gives 409 for, so they sit with the state gates that already meant
# exactly that. `persistence` is the machinery failing to answer and stays 503.
# 403 was rejected: it would read plan-level consent as session-level auth.
#
# The rule decides these seven; it has not been applied to the whole guarded
# vocabulary, and saying otherwise would be the confident half-truth this set
# exists to remove. What is settled, and what is not:
#
#   Not reachable here. `authority_binding` and `claimed` look state-shaped but
#   no callable `field_effect_call` wraps can raise them -- the first is a
#   planning code and planning runs on the scheduler, the second belongs to
#   `abandon`, which no route calls. Their status is not a live question.
#   The `_stale` reasons (`dependency`, `target_changed`, ...) are not errors at
#   all: they are recorded on a 200 response, never raised.
#
#   Reachable and open. `manual_hold` is a refusal but reads as plausibly 404
#   as 409; `not_ready` is changed by a scan as much as by a decision; and
#   `enable`/`revoke` raise `consent` for malformed input, where 409 asserts a
#   state conflict about an argument the caller simply got wrong. The `invalid_*`
#   family answers 503 though it is client-shaped. None was reclassified here:
#   each needs a decision this change did not take.
# (#678)
_FIELD_EFFECT_STATE_CONFLICT = frozenset(
    {
        "authority",
        "capability",
        "claim_conflict",
        "consent",
        "draining",
        "paused",
        "transition_conflict",
    }
)

_Result = TypeVar("_Result")
_SessionDependency: TypeAlias = Callable[[Request, str | None], str]


class _LiveUpdateStreamingResponse(StreamingResponse):
    """Release a reserved reader even when the response never reaches its body."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        subscription: LiveUpdateSubscription,
        *,
        media_type: str,
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self._subscription = subscription

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._subscription.close()


class _LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    password: StrictStr = Field(min_length=1, max_length=1024)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _SettingsWriteRequest(_ClosedModel):
    expected_revision: StrictInt = Field(ge=1)
    document: OwnerSettingsDocument
    secrets: list[SecretWrite] = Field(max_length=32)


class _AppearanceResponse(_ClosedModel):
    reduced_motion: Literal["follow_system", "always_reduced"]


class _ReviewerRevisionRequest(_ClosedModel):
    expected_revision: StrictInt = Field(ge=1)


class _ReviewerSelectionRequest(_ReviewerRevisionRequest):
    task: Literal["cover_observation", "description_rewrite"] = "cover_observation"
    engine: Literal["codex"] | None


class _OpenRouterWriteRequest(_ReviewerRevisionRequest):
    task: Literal["cover_observation", "description_rewrite"] = "cover_observation"
    api_key: SecretStr = Field(json_schema_extra={"writeOnly": True})
    model: OpenRouterModelId


class _CodexDeviceAuthResponse(_ClosedModel):
    state: DeviceAuthStatus
    verification_url: StrictStr | None = None
    user_code: StrictStr | None = None
    expires_in_seconds: StrictInt | None = None
    failure: DeviceAuthFailure | None = None


class _CodexDeviceStartResponse(_ClosedModel):
    access: ReviewerAccessView
    device: _CodexDeviceAuthResponse


class _SeriesValue(_ClosedModel):
    name: str
    sequence: str | None


class _TargetField(_ClosedModel):
    destination: str
    state: str
    value: str | bool | list[str] | list[_SeriesValue] | None


class _ProposedValue(_ClosedModel):
    kind: str
    value: str | bool | list[str] | list[_SeriesValue]


class _DecisionProjection(_ClosedModel):
    actor: str
    application: Literal["apply", "recorded_only"]
    application_reason: str | None = Field(default=None, max_length=256)
    decision_id: str
    destination: str
    occurred_at: int
    outcome: str
    proposal_id: str
    review_case_id: str
    selected_value: _ProposedValue | None
    target_fingerprint: str


class _AutomaticDecisionProjectionBase(_ClosedModel):
    candidate_id: str | None
    decision_id: str
    destination: str
    occurred_at: int
    outcome: str
    proposal_id: str
    review_case_id: str
    selected_value: _ProposedValue | None
    target_fingerprint: str


class _AutomaticDecisionProjection(_AutomaticDecisionProjectionBase):
    destination: Literal[
        "title",
        "subtitle",
        "authors",
        "narrators",
        "series",
        "genres",
        "publishedYear",
        "publishedDate",
        "publisher",
        "description",
        "isbn",
        "asin",
        "language",
        "explicit",
        "tags",
    ]


class _CoverAutomaticDecisionProjection(_AutomaticDecisionProjectionBase):
    candidate_id: str
    destination: Literal["cover"]
    outcome: Literal["candidate"]
    supersedes_decision_id: str | None


class _AutomaticPauseRequest(_ClosedModel):
    paused: bool


class _AutomaticRecoveryRequest(_ClosedModel):
    category: StrictStr | None


class _AutomaticSuspendRequest(_ClosedModel):
    category: StrictStr | None = None
    reason: StrictStr = Field(min_length=1, max_length=256)


class _ActivityConsentGrant(_ClosedModel):
    library: StrictStr = Field(min_length=1, max_length=128)
    destinations: list[StrictStr] = Field(min_length=1, max_length=64)


class _ActivityConsentRequest(_ClosedModel):
    grants: list[_ActivityConsentGrant] = Field(min_length=1, max_length=64)


class _CoverIdentity(_ClosedModel):
    candidate_reference: str
    evidence_references: list[str]
    outcome: str
    preview_reference: str
    reasons: list[str]


class _CoverObservation(_ClosedModel):
    candidate_reference: str
    composition: Literal["dense", "minimal", "unknown"]
    cover_text_language: Literal[
        "english", "non_english", "multiple", "no_visible_title", "unknown"
    ]
    explicit_imagery: Literal["absent", "suggestive", "explicit", "unknown"]
    format_branding: Literal["absent", "text_edition", "audiobook", "unknown"]
    medium: Literal["illustrated", "mixed", "photographic", "typographic", "unknown"]
    overlay_marking: Literal["absent", "present", "unknown"]
    placeholder: Literal["absent", "present", "unknown"]
    preview_sha256: str
    preview_variant: str
    promotional_text: Literal["absent", "present", "unknown"]
    reproduction: Literal["clean", "degraded", "unknown"]
    series_styling: Literal["standalone", "series_branded", "omnibus_or_set", "unknown"]
    thumbnail_legibility: Literal["legible", "illegible", "unknown"]
    tie_in_branding: Literal["absent", "present", "unknown"]


class _CoverCandidate(_ClosedModel):
    artifact_id: str
    deterministic_citations: list[str]
    deterministic_rank: int | None
    deterministic_rank_key: list[int | str] | None
    height: int
    identity: _CoverIdentity
    incumbent: bool
    match_state: Literal["same_edition", "same_book", "match_not_confirmed"]
    observation: _CoverObservation | None
    original_sha256: str
    ordinal: int
    preview_sha256: str
    reference: str
    selectable: bool
    source_kinds: list[str]
    state: str
    transport_sha256: str
    width: int


class _CoverOmission(_ClosedModel):
    preview_bytes: int | None = None
    preview_sha256: str | None = None
    reason: str
    reference: str
    retained_reference: str | None = None


class _CoverIncumbent(_ClosedModel):
    reference: str | None
    state: Literal["absent", "available", "unavailable"]


class _CoverPolicyPreferences(_ClosedModel):
    avoid_promotional_text: bool
    avoid_tie_in_branding: bool
    prefer_minimal_composition: bool
    schema_version: Literal["cover-policy-v1"]


class _CoverPolicyReport(_ClosedModel):
    acceptable_candidate_references: list[str]
    actual_target_state: Literal["known_empty", "confirmed", "unknown"]
    cover_packet_reference: str
    identity_classifier_reference: str
    identity_report_sha256: str
    observation_report_sha256: str | None
    observation_taxonomy_reference: str
    owner_ready: bool
    policy_reference: str
    reasons: list[str]
    recommended_candidate_reference: str | None
    schema_version: Literal["cover-policy-report-v2"]
    status: Literal["recommended", "abstained"]


class _CoverConsiderationResponse(_ClosedModel):
    candidates: list[_CoverCandidate] = Field(max_length=12)
    choice_contexts: list["_ChoiceContext"]
    consideration_id: str
    current: bool
    incumbent: _CoverIncumbent
    omissions: list[_CoverOmission]
    policy: _CoverPolicyReport | None
    policy_preferences: _CoverPolicyPreferences
    policy_reference: str
    provider_fetching: bool
    ready: bool
    reviewer_engine: Literal["codex", "openrouter"] | None
    reviewer_model: str | None
    reviewer_access_revision: StrictInt | None
    reviewer_status: str
    reviewer_unavailable_reason: str | None
    schema_version: Literal["cover-consideration-v4"]
    supersedes_id: str | None
    target_fingerprint: str


class _SelectedDescriptionAdvice(_ClosedModel):
    candidate: DescriptionAdviceCandidate
    binding: DescriptionAdviceBinding
    current: bool


class _DescriptionAdviceResponse(_ClosedModel):
    state: Literal["requested", "running", "succeeded", "failed", "interrupted"]
    failure: Literal["unavailable", "invalid_output", "interrupted"] | None
    engine: Literal["codex", "openrouter"]
    model: str
    attempt_id: str
    current: bool
    candidate: DescriptionAdviceCandidate | None
    binding: DescriptionAdviceBinding
    selection: AuthoredDescriptionDecision | None
    selected_advice: _SelectedDescriptionAdvice | None
    application_outcome: Literal["verified", "no_change_required"] | None

    @model_validator(mode="after")
    def coherent_advice(self) -> _DescriptionAdviceResponse:
        if (self.selection is None) != (self.selected_advice is None):
            raise ValueError("description selection snapshot mismatch")
        if (
            self.selection is not None
            and self.selected_advice is not None
            and (
                self.selection.candidate_id
                != self.selected_advice.candidate.candidate_id
                or self.selection.binding != self.selected_advice.binding
            )
        ):
            raise ValueError("description selection snapshot mismatch")
        if self.application_outcome is not None and self.selection is None:
            raise ValueError("description outcome requires selection")
        if self.candidate is not None and (
            self.candidate.attempt_id != self.attempt_id
            or self.candidate.engine != self.engine
            or self.candidate.model != self.model
        ):
            raise ValueError("description candidate attempt mismatch")
        return self


class _DescriptionSelectionRequest(_ClosedModel):
    candidate_id: str = Field(pattern=r"^description_candidate_[0-9a-f]{64}$")
    binding: DescriptionAdviceBinding


class _DescriptionRetryAcknowledgement(_ClosedModel):
    queued: Literal[True]


class _ReviewField(_ClosedModel):
    classification: Literal[
        "needs_attention",
        "manual_fallback",
        "automatic_recorded",
        "human_recorded",
        "unsupported",
    ]
    classification_reason: str
    current: _TargetField
    deterministic_disposition: str
    destination: str
    decision_actions: list[Literal["accept", "keep_current"]]
    editable: bool
    effect_capability: str
    human_decision: _DecisionProjection | None
    inventory_role: Literal["primary", "contextual"]
    automatic_decision: (
        _AutomaticDecisionProjection | _CoverAutomaticDecisionProjection | None
    )
    automatic_withheld_reason: str | None = Field(default=None, max_length=512)
    automatic_selection_sources: list[str]
    manual_hold: bool
    manual_required: bool
    needs_attention: bool
    navigation_role: Literal["attention", "inspect"]
    notices: list[str]
    proposed_values: list[_ProposedValue]
    choice_contexts: list["_ChoiceContext"]
    provenance: list[str]
    preservation_only: bool
    corroboration_sources: list[str]
    proposal_id: str
    target_fingerprint: str
    uncertainty: list[str]
    cover_consideration: _CoverConsiderationResponse | None = None
    description_advice: _DescriptionAdviceResponse | None = None


class _ChoiceEvidence(_ClosedModel):
    observation: str
    role: Literal["external_corroboration", "local_presence"]
    source: str


class _ChoiceContext(_ClosedModel):
    alternative_id: str | None
    evidence: list[_ChoiceEvidence]
    limitations: list[str]
    rationale: str
    recommendation: Literal["no_recommendation", "recommended"]
    recommendation_reason: str
    selection: Literal["current", "proposed"]


class _TargetIdentity(_ClosedModel):
    authors: list[Annotated[str, Field(max_length=512)]] = Field(max_length=128)
    connection: str = Field(max_length=512)
    item: str = Field(max_length=512)
    library: str = Field(max_length=512)
    media_kind: str = Field(max_length=64)
    title: str = Field(max_length=512)


class _RecordingIdentityBasis(_ClosedModel):
    basis: Literal["chapter_runtime_and_structure"]
    provider: StrictStr = Field(min_length=1, max_length=128)
    candidate: StrictStr = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def canonical_references(self) -> Self:
        for value in (self.provider, self.candidate):
            if not value.strip() or any(
                ord(character) < 32 or ord(character) == 127 for character in value
            ):
                raise ValueError("recording identity reference is invalid")
        return self


class _CaseDetailResponse(_ClosedModel):
    recording_identity_basis: _RecordingIdentityBasis | None
    comparison_notices: list[str]
    current: bool
    fields: list[_ReviewField]
    # The same section the recorded-evidence view carries, under the same
    # bound: a work with a review case is the one an owner opens first, and a
    # recording that runs short of its edition is not a field-level finding
    # any of `fields` could state.
    media_health: list[_MediaHealthObservation] = Field(
        max_length=MAX_HEALTH_OBSERVATIONS
    )
    review_case_id: str
    schema_version: Literal["review-case-detail-v4"]
    target: _TargetIdentity
    writes_consented: bool


class _ConfirmedDecision(_ClosedModel):
    status: Literal["confirmed"]
    case: _CaseDetailResponse


class _UnavailableDecisionConfirmation(_ClosedModel):
    status: Literal["unavailable"]


_DecisionConfirmation: TypeAlias = Annotated[
    _ConfirmedDecision | _UnavailableDecisionConfirmation, Field(discriminator="status")
]


class _DescriptionSelectionAcknowledgement(_ClosedModel):
    schema_version: Literal["description-selection-ack-v1"] = (
        "description-selection-ack-v1"
    )
    confirmation: _DecisionConfirmation
    decision_id: str
    advised: Literal[True]
    invalidation_sequences: list[int]


class _CaseSuccessorResponse(_ClosedModel):
    review_case_id: str | None


class _EvidenceItem(_ClosedModel):
    destinations: list[Annotated[StrictStr, Field(max_length=64)]] = Field(
        min_length=1, max_length=16
    )
    observation: str = Field(max_length=1024)
    role: Literal["external_corroboration", "external_observation", "local_presence"]
    source: str = Field(max_length=128)


class _EvidenceResponse(_ClosedModel):
    items: list[_EvidenceItem] = Field(max_length=50)
    missing_lineage_count: int = Field(ge=0, le=10_000)
    next_cursor: str | None
    review_case_id: str
    schema_version: Literal["review-case-evidence-v4"]
    source_ledger: list["_ProviderOutcome"] = Field(max_length=100)


class _HistoryTransitionItem(_ClosedModel):
    checkpoint: int = Field(ge=0)
    kind: str = Field(max_length=64)
    reason: str = Field(max_length=512)
    transition_id: str = Field(max_length=128)


class _HistoryDecisionItem(_ClosedModel):
    actor: Literal["owner"]
    application: Literal["apply", "recorded_only"] | None
    application_reason: str | None = Field(default=None, max_length=256)
    decision_id: str
    destination: str
    event_id: str
    kind: Literal["human_decision_recorded"]
    occurred_at: int
    outcome: Literal["accept", "keep_current", "reject"]
    previous_value: _ProposedValue | None
    review_case_id: str
    schema_version: Literal["human-decision-event-v1"]
    selected_value: _ProposedValue | None


class _HistoryShadowItem(_ClosedModel):
    authority: Literal["none"]
    dispositions: dict[str, int] = Field(max_length=32)
    kind: Literal["shadow_preview_recorded"]
    occurred_at: int
    origin: Literal["policy_preview"]
    outcome_set_id: str
    review_case_id: str
    schema_version: Literal["shadow-preview-history-event-v1"]


class _HistoryAutomaticItem(_ClosedModel):
    decision_id: str
    destination: str
    kind: Literal["automatic_decision_recorded"]
    occurred_at: int
    origin: Literal["automatic_policy"]
    outcome: Literal["candidate", "keep_current", "ignore"]
    previous_value: _ProposedValue | None
    review_case_id: str
    schema_version: Literal["automatic-decision-history-event-v1"]
    selected_value: _ProposedValue | None


class _HistoryCoverItem(_ClosedModel):
    consideration_id: str
    consideration: _CoverConsiderationResponse
    decision: _DecisionProjection | _CoverAutomaticDecisionProjection | None
    decision_id: str | None
    kind: Literal[
        "automatic_prepared",
        "owner_retry_prepared",
        "superseded",
        "selected",
        "kept_incumbent",
        "no_action",
        "stale",
    ]
    occurred_at: int
    review_case_id: str
    schema_version: Literal["cover-consideration-event-v1"]


class _HistoryDescriptionItem(_ClosedModel):
    schema_version: Literal["description-advice-history-v1"]
    kind: Literal[
        "description_advice_recorded", "description_selected", "description_application"
    ]
    occurred_at: int
    review_case_id: str
    state: Literal[
        "requested",
        "running",
        "succeeded",
        "failed",
        "interrupted",
        "planned",
        "claimed",
        "applying",
        "reconciling",
        "retryable",
        "verified",
        "stale",
        "superseded",
        "abandoned",
        "no_change_required",
    ]
    engine: Literal["codex", "openrouter"]
    model: str
    candidate: DescriptionAdviceCandidate | None
    decision: AuthoredDescriptionDecision | None


class _HistoryResponse(_ClosedModel):
    items: list[
        _HistoryTransitionItem
        | _HistoryDecisionItem
        | _HistoryShadowItem
        | _HistoryAutomaticItem
        | _HistoryCoverItem
        | _HistoryDescriptionItem
    ] = Field(max_length=150)
    next_cursor: str | None
    review_case_id: str
    schema_version: Literal["review-case-history-v2"]


class _SessionResponse(_ClosedModel):
    actor: str
    csrf_token: str
    expires_at: int
    schema_version: str


class _ErrorCode(_ClosedModel):
    code: str
    managed_by_file: Literal["/data/config.json"] | None = None


class _ErrorResponse(_ClosedModel):
    error: _ErrorCode
    schema_version: str


class _ReviewCounts(_ClosedModel):
    all: int
    needs_attention: int
    no_attention_needed: int


class _MediaHealthObservation(_ClosedModel):
    """One typed statement about an item's media, with its citation and bound.

    The same closed shape on both surfaces that carry it: the report detail
    derives its list from retained evidence, the terminal row from the record
    of what stopped, and a reader switches on `kind` in one vocabulary.
    """

    bound: str = Field(max_length=256)
    # Never empty: an observation an owner cannot check against the report is
    # an assertion, and the domain shape refuses to build one. The wire says
    # the same rather than leaving a reader to discover it.
    cited: list[str] = Field(min_length=1, max_length=520)
    expected: str | None = Field(default=None, max_length=256)
    # Closed on the wire so a client can switch on `kind` exhaustively and a
    # value outside the vocabulary fails validation here rather than reaching
    # a reader as a statement it cannot render. The thirteen values are
    # transcribed rather than taken from `AudioHealthKind` because this model
    # validates strictly, which would demand enum members where the projection
    # hands it strings; `tests/unit/test_review_web_read_models.py` pins this
    # set equal to the domain's so the two cannot drift.
    kind: Literal[
        "runtime_short_of_provider",
        "chapter_structure_mismatch",
        "track_sequence_gap",
        "track_sequence_disorder",
        "track_duration_unavailable",
        "chapter_atoms_partial",
        "tag_disagrees_across_tracks",
        "asin_disagrees_across_tracks",
        "container_unexpected",
        "codec_unexpected",
        "duration_unstated",
        "stream_encrypted",
        "media_unreadable",
    ]
    observed: str | None = Field(default=None, max_length=256)
    tracks: list[int] = Field(max_length=512)


class _PreparationFailure(_ClosedModel):
    attempt: int
    diagnostic_reference: str
    media_health: list[_MediaHealthObservation] = Field(max_length=512)
    provider: str
    reason: str
    recovery: str
    recovery_attempt: int
    stage: str
    status: str


class _ProviderOutcome(_ClosedModel):
    provider_requests: int = Field(ge=0)
    lane_kind: str = Field(max_length=64)
    provider: str = Field(max_length=128)
    reason: str = Field(max_length=512)
    status: str = Field(max_length=64)


class _LibraryWorkSummary(_ClosedModel):
    fields: list[_TargetField]
    reason: str
    state: str


class _LibraryWork(_ClosedModel):
    authors: list[str]
    attention_count: int
    attention_state: str
    detail_url: str | None
    # `case` opens a review case, `report` opens the recorded evidence of a
    # work that has none, and `None` means there is nothing to open. Closed
    # here so a client switches on the kind rather than on the URL's shape.
    detail_kind: Literal["case", "report"] | None
    dispositions: list[str]
    item: str
    library: str
    media_kind: str
    review_case_id: str | None
    summary: _LibraryWorkSummary | None = None
    title: str
    work_id: str
    preparation: _PreparationFailure | None = None
    preparation_outcomes: list[_ProviderOutcome]


class _ReportChapter(_ClosedModel):
    end_milliseconds: int = Field(ge=0)
    ordinal: int = Field(ge=1)
    start_milliseconds: int = Field(ge=0)
    title: str | None = Field(default=None, max_length=280)


class _ReportEmbeddedCover(_ClosedModel):
    present: bool
    surface: str | None = Field(default=None, max_length=64)


class _ReportEvidence(_ClosedModel):
    basis: str = Field(max_length=64)
    excerpt: str = Field(max_length=1024)
    kind: str = Field(max_length=64)
    role: str | None = Field(default=None, max_length=64)
    state: str = Field(max_length=32)
    value: str = Field(max_length=1024)


class _ReportProviderEvidence(_ClosedModel):
    candidate: str = Field(max_length=128)
    kind: str = Field(max_length=64)
    provider: str = Field(max_length=128)
    role: str | None = Field(default=None, max_length=64)
    state: str = Field(max_length=32)
    value: str | None = Field(default=None, max_length=16_384)


class _ReportDetailResponse(_ClosedModel):
    recording_identity_basis: _RecordingIdentityBasis | None
    chapters: list[_ReportChapter] = Field(max_length=1024)
    chapters_truncated: bool
    connection: str = Field(max_length=512)
    embedded_cover: _ReportEmbeddedCover
    # Nothing in the domain caps a report's claim count -- only the report's
    # own byte ceiling does -- so the projection truncates to this bound and
    # says it did, rather than the transport refusing a claim-rich work.
    evidence: list[_ReportEvidence] = Field(max_length=1024)
    evidence_truncated: bool
    item: str = Field(max_length=512)
    library: str = Field(max_length=512)
    media_kind: str = Field(max_length=32)
    # What is wrong with the media, separate from what the evidence says about
    # the work. Bounded by the domain's own ceiling of one observation per kind
    # per track.
    media_health: list[_MediaHealthObservation] = Field(
        max_length=MAX_HEALTH_OBSERVATIONS
    )
    # The two provider surfaces: what every configured provider was asked and
    # how it ended, and what the admitted candidates stated. Both are bounded
    # exactly as the local evidence above is.
    provider_sources: list[_ProviderOutcome] = Field(max_length=1024)
    provider_evidence: list[_ReportProviderEvidence] = Field(max_length=1024)
    provider_evidence_truncated: bool
    report_id: str = Field(max_length=128)
    schema_version: Literal["review-report-detail-v4"]
    title: str = Field(max_length=1024)


class _LibraryResponse(_ClosedModel):
    counts: _ReviewCounts
    items: list[_LibraryWork]
    lens: str
    next_cursor: str | None
    page: int
    previous_cursor: str | None
    query: str | None
    # Closed, like every other read model's discriminator. A bounded string let
    # the library token move without the wire contract moving with it, which is
    # how `review-library-v1` gained a required `media_health` member and stayed
    # v1; the literal makes the declared version and the served shape the same
    # statement, and publishes it in the OpenAPI a client is built from.
    schema_version: Literal["review-library-v2"]


class _AttentionItem(_ClosedModel):
    authors: list[str]
    classification_reason: str
    destination: str
    detail_url: str
    item: str
    library: str
    media_kind: str
    notices: list[str]
    provenance: list[str]
    review_case_id: str
    title: str
    work_attention_count: int


class _AttentionResponse(_ClosedModel):
    items: list[_AttentionItem]
    next_cursor: str | None
    query: str | None
    schema_version: str
    total: int


class _ActivityPrepared(_ClosedModel):
    automatic: int
    human: int
    total: int


class _ActivityLibrary(_ClosedModel):
    connection: str
    library: str
    prepared: _ActivityPrepared | None = None


class _ActivityDestination(_ClosedModel):
    consented: bool
    destination: str
    prepared: int = Field(default=0, ge=0)
    state: str


class _ActivityCapability(_ClosedModel):
    connection: str
    destinations: list[_ActivityDestination]
    library: str
    state: str


class _ActivityEvidence(_ClosedModel):
    label: str = Field(max_length=64)
    value: str = Field(max_length=512)


class _ActivityPolicyChange(_ClosedModel):
    field: str = Field(max_length=64)
    setting: str = Field(max_length=64)
    previous: str = Field(max_length=512)
    next: str = Field(max_length=512)


class _ActivityDiagnostic(_ClosedModel):
    event_class: Literal["automatic_decision"]
    reason: Literal["automatic_authority_unavailable"]


class _ActivityEvent(_ClosedModel):
    author_label: str = Field(default="Author details are not recorded", max_length=512)
    # What the owner reads across the row, in order: which work or setting
    # changed, the exact change, and the result. ``change_detail`` is the same
    # change as a whole sentence, for the reader who hears the row rather than
    # seeing the arrow (#636).
    category: Literal["owner", "automatic", "policy", "recovery"] = "recovery"
    change: str = Field(default="Recorded", max_length=512)
    change_id: str = Field(default="recorded-change", max_length=128)
    change_detail: str = Field(default="Recorded", max_length=512)
    destination: str = Field(max_length=512)
    diagnostic: _ActivityDiagnostic | None = None
    event_id: str = Field(default="recorded-event", max_length=128)
    event_type: str = Field(default="unknown", max_length=32)
    evidence: list[_ActivityEvidence] = Field(default_factory=list, max_length=8)
    failure: str | None = Field(default=None, max_length=512)
    field_label: str = Field(default="Recorded field", max_length=64)
    # A Policy row's exact From/To. Every other category keeps the compact
    # detail density rather than inheriting policy-detail bulk.
    policy_changes: list[_ActivityPolicyChange] = Field(
        default_factory=list, max_length=32
    )
    library: str = Field(max_length=512)
    occurred_at: int | None = None
    origin: str = Field(max_length=512)
    state: str = Field(max_length=512)
    transition_id: str | None = Field(default=None, max_length=128)
    work_label: str = Field(default="Recorded work", max_length=512)
    work_id: str = Field(max_length=512)


class _ActivityTransition(_ClosedModel):
    event_id: str = Field(max_length=128)
    occurred_at: int
    sequence: int = Field(ge=1)
    state: str = Field(max_length=512)


class _ActivityTransitions(_ClosedModel):
    events: list[_ActivityTransition] = Field(max_length=20)
    has_more: bool = False


class _ActivitySuspension(_ClosedModel):
    connection: str
    destination: str | None
    reason: str
    scope: str
    updated_at: int


class _ActivityExample(_ClosedModel):
    after: object
    before: object
    destination: str
    library: str
    work_id: str


class _ActivityLedger(_ClosedModel):
    event_type: Literal["all", "review", "policy", "metadata", "safety", "unknown"]
    page: int = Field(ge=1, le=1_000_000)
    page_count: int = Field(ge=1, le=1_000_000)
    page_size: Literal[25, 50, 100]
    query: str | None = Field(default=None, max_length=128)
    range_days: Literal[7, 30, 90]
    total: int = Field(ge=0)


class _ActivityWaitingReasons(_ClosedModel):
    applying: int = Field(ge=0)
    awaiting_permission: int = Field(ge=0)
    held_for_recovery: int = Field(ge=0)
    retrying: int = Field(ge=0)
    scheduled: int = Field(ge=0)


class _ActivityWaiting(_ClosedModel):
    destination: str = Field(max_length=512)
    library: str = Field(max_length=512)
    state: str = Field(max_length=512)
    work_id: str = Field(max_length=512)
    work_label: str = Field(max_length=512)


class _ProviderRecoveryActivity(_ClosedModel):
    affected_count: int = Field(ge=1, le=1_000_000)
    dispatch_reasons: list[
        Literal[
            "missing_corroboration",
            "zero_provider_evidence",
            "settleable_disagreement",
            "blanket_scan",
        ]
    ] = Field(default_factory=list, max_length=4)
    next_eligible_at: int | None = Field(default=None, ge=0, le=8_640_000_000_000)
    state: Literal["waiting", "checking", "dormant", "recovered", "fanout", "exhausted"]


class _ActivityResponse(_ClosedModel):
    backup_ready: bool
    capabilities: list[_ActivityCapability]
    configured_libraries: list[_ActivityLibrary]
    consented_libraries: list[_ActivityLibrary]
    counts: dict[str, int]
    enabled_libraries: list[_ActivityLibrary]
    events: list[_ActivityEvent]
    example: _ActivityExample | None
    last_failure: str | None
    ledger: _ActivityLedger
    current_waiting: _ActivityWaiting | None
    paused: bool
    prepared: _ActivityPrepared
    provider_recovery: list[_ProviderRecoveryActivity] = Field(
        default_factory=list, max_length=100
    )
    schema_version: Literal["continuous-field-activity-v2"]
    suspensions: list[_ActivitySuspension]
    waiting: _ActivityWaitingReasons


class _PreparationStage(_ClosedModel):
    stage: Literal[
        "queued",
        "extracting",
        "providers",
        "proposals",
        "admission",
        "projection",
        "ready",
        "recorded",
        "failed",
    ]
    count: int = Field(ge=0)


class _PreparationProgress(_ClosedModel):
    scan_id: str | None
    connection: str
    library: str
    library_id: str
    state: str
    checked: int
    total: int
    ready: int
    blocked: int
    recorded: int
    rescan_requested: bool
    stages: list[_PreparationStage] = Field(default_factory=list, max_length=9)


class _PreparationConnection(_ClosedModel):
    connection: str
    code: Literal["tls", "authentication", "authorization", "unavailable"]
    state: Literal["paused", "checking", "recovered"]
    affected: int = Field(ge=0)
    updated_at: int = Field(ge=0)


class _PreparationTriggerRecord(_ClosedModel):
    trigger: Literal["initial_setup", "manual_scan", "library_added"]
    accepted_count: int = Field(ge=1)
    first_accepted_at: int = Field(ge=0)
    last_accepted_at: int = Field(ge=0)


class _PreparationPassage(_ClosedModel):
    read: int = Field(ge=0)
    searches_finished: int = Field(ge=0)
    compared: int = Field(ge=0)
    checked: int = Field(ge=0)
    ready: int = Field(ge=0)
    failed: int = Field(ge=0)


class _PreparationActiveBook(_ClosedModel):
    name: StrictStr = Field(min_length=1, max_length=2048)
    stage: str


class _PreparationCompletion(_ClosedModel):
    name: StrictStr = Field(min_length=1, max_length=2048)
    outcome: Literal["ready", "failed"]
    completed_at: int = Field(ge=0)


class _PreparationPresentation(_ClosedModel):
    run_id: StrictStr = Field(pattern=r"^preparation_run_[0-9a-f]{64}$")
    state: Literal[
        "starting",
        "running",
        "blocked",
        "completed_clean",
        "completed_with_failures",
        "dismissed",
    ]
    triggers: list[_PreparationTriggerRecord] = Field(min_length=1, max_length=3)
    passage: _PreparationPassage
    active_books: list[_PreparationActiveBook] = Field(max_length=5)
    active_overflow: int = Field(ge=0)
    recent_completions: list[_PreparationCompletion] = Field(max_length=8)
    throughput_per_minute: float = Field(ge=0)
    started_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)
    completed_at: int | None = Field(default=None, ge=0)
    total: int = Field(ge=0)
    dismissible: bool


class _PreparationDismissRequest(_ClosedModel):
    run_id: StrictStr = Field(pattern=r"^preparation_run_[0-9a-f]{64}$")


class _PreparationResponse(_ClosedModel):
    libraries: list[_PreparationProgress]
    connections: list[_PreparationConnection] = Field(default_factory=list)
    paused: bool = False
    active: bool = False
    actionable: bool = False
    recovery_required: bool = False
    first_boot: bool = False
    first_boot_complete: bool = False
    first_boot_works: int | None = Field(default=None, ge=0)
    presentation: _PreparationPresentation | None = None
    schema_version: Literal["review-preparation-v2"] = "review-preparation-v2"


class _DecisionRequest(_ClosedModel):
    destination: StrictStr = Field(min_length=1, max_length=64)
    outcome: Literal["accept", "keep_current", "reject"]
    proposal_id: StrictStr = Field(pattern=r"^review_proposal_[0-9a-f]{64}$")
    selected_value: _ProposedValue | None = None
    target_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    consideration_id: StrictStr | None = Field(
        default=None, pattern=r"^cover_consideration_[0-9a-f]{64}$"
    )


class _DecisionAcknowledgement(_DecisionProjection):
    confirmation: _DecisionConfirmation
    # The stream positions this decision's own announcements were given, so the
    # tab that recorded it can recognise its echo. Three: the case, its Library
    # row, and the write queue. Bounded because it is a fixed set of
    # announcements, not a log.
    invalidation_sequences: list[int] = Field(max_length=8)
    schema_version: str


class _PolicyDraftRequest(_ClosedModel):
    name: StrictStr = Field(min_length=1, max_length=80)
    options: dict[StrictStr, StrictStr] = Field(max_length=16)
    destinations: dict[StrictStr, StrictStr] = Field(max_length=16)


class _PolicyDraftResponse(_ClosedModel):
    policy_id: str
    schema_version: Literal["metadata-policy-profile-v3"] = "metadata-policy-profile-v3"


class _PolicyProfile(_ClosedModel):
    destinations: dict[StrictStr, StrictStr] = Field(max_length=16)
    name: StrictStr = Field(min_length=1, max_length=80)
    options: dict[StrictStr, StrictStr] = Field(max_length=16)
    schema_version: Literal["metadata-policy-profile-v3"]


class _RichQualificationBase(_ClosedModel):
    candidate_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class _SubtitleFillQualification(_RichQualificationBase):
    automatic_available: Literal[True]
    disposition: Literal["passing_automatic"]
    field: Literal["subtitle"]
    option: Literal["fill_empty"]


class _SubtitleSplitQualification(_RichQualificationBase):
    automatic_available: Literal[False]
    disposition: Literal["not_qualified"]
    field: Literal["subtitle"]
    option: Literal["corroborated_split"]


class _SeriesQualification(_RichQualificationBase):
    automatic_available: Literal[True]
    disposition: Literal["passing_automatic"]
    field: Literal["series"]
    option: Literal["primary_narrative", "source_order", "all_qualified"]


class _GenresQualification(_RichQualificationBase):
    automatic_available: Literal[True]
    disposition: Literal["passing_automatic"]
    field: Literal["genres"]
    option: Literal["fill_empty", "add", "replace"]


class _TagsOffQualification(_RichQualificationBase):
    automatic_available: Literal[False]
    disposition: Literal["policy_only"]
    field: Literal["tags"]
    option: Literal["off"]


class _TagsQualification(_RichQualificationBase):
    automatic_available: Literal[True]
    disposition: Literal["passing_automatic"]
    field: Literal["tags"]
    option: Literal["fill_empty", "add", "replace"]


class _DescriptionQualification(_RichQualificationBase):
    automatic_available: Literal[True]
    disposition: Literal["passing_automatic"]
    field: Literal["description_fill"]
    option: Literal["safe_plain_text"]


class _GoogleDescriptionQualification(_RichQualificationBase):
    automatic_available: Literal[False]
    disposition: Literal["not_qualified"]
    field: Literal["description_fill"]
    option: Literal["google_visible_text"]


class _IsbnQualification(_RichQualificationBase):
    automatic_available: Literal[True]
    disposition: Literal["passing_automatic"]
    field: Literal["isbn_form"]
    option: Literal["preserve_exact", "prefer_isbn13"]


class _AsinQualification(_RichQualificationBase):
    automatic_available: Literal[False]
    disposition: Literal["insufficient_coverage"]
    field: Literal["asin"]
    option: Literal["preserve_exact", "ebook_manifestation"]


class _ExplicitQualification(_RichQualificationBase):
    automatic_available: Literal[False]
    disposition: Literal["passing_human_only"]
    field: Literal["explicit"]
    option: Literal["unsupported"]


class _CoverQualification(_RichQualificationBase):
    automatic_available: Literal[True]
    disposition: Literal["pass"]
    field: Literal["cover_order"]
    option: Literal["fixed_source_order"]


_RichQualification: TypeAlias = (
    _SubtitleFillQualification
    | _SubtitleSplitQualification
    | _SeriesQualification
    | _GenresQualification
    | _TagsOffQualification
    | _TagsQualification
    | _DescriptionQualification
    | _GoogleDescriptionQualification
    | _IsbnQualification
    | _AsinQualification
    | _ExplicitQualification
    | _CoverQualification
)
_RICH_QUALIFICATION_ADAPTER: TypeAdapter[_RichQualification] = TypeAdapter(
    _RichQualification
)


class _AudiobookQualification(_ClosedModel):
    """One audiobook decision tranche and whether a record qualified it.

    `field` and `option` are the owner's existing policy category and option,
    so a routing surface can read this exactly as it reads the rich records;
    `identity` names the audiobook shape the record qualified and never
    speaks for an ebook. Availability and the digest move together when unit
    C publishes a record, so the shape is closed but neither is a `Literal`.
    """

    automatic_available: StrictBool
    destination: Literal[
        "title",
        "subtitle",
        "authors",
        "narrators",
        "series",
        "genres",
        "tags",
        "description",
        "publishedDate",
        "publisher",
        "language",
        "asin",
        "cover",
        "isbn",
        "explicit",
    ]
    field: Literal[
        "title_layout",
        "subtitle",
        "contributor_names",
        "series",
        "genres",
        "tags",
        "description_fill",
        "date_precision",
        "publisher_preference",
        "language_rendering",
        "asin",
        "cover_order",
        "isbn_form",
        "explicit",
    ]
    identity: StrictStr = Field(max_length=128, pattern=r"^[a-z_]+:[a-z0-9_]+:[a-z_]+$")
    option: Literal[
        "preserve",
        "title_and_subtitle",
        "fill_empty",
        "corroborated_split",
        "canonical",
        "primary_narrative",
        "source_order",
        "all_qualified",
        "add_qualified",
        "replace_qualified",
        "add",
        "replace",
        "safe_plain_text",
        "replace_safe_plain_text",
        "year",
        "full_date",
        "publisher",
        "imprint",
        "display_name",
        "preserve_exact",
        "fixed_source_order",
        "replace_descriptive",
        "prefer_isbn13",
        "adult_product",
        "replace_if_strictly_better",
    ]
    operation: Literal[
        "fill",
        "preserve",
        "normalize",
        "add",
        "replace",
        "replace_invalid",
        "replace_recording",
    ]
    record_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


_AUDIOBOOK_QUALIFICATION_ADAPTER: TypeAdapter[_AudiobookQualification] = TypeAdapter(
    _AudiobookQualification
)


class _PolicyProfileResponse(_ClosedModel):
    audiobook_qualification: list[_AudiobookQualification]
    policy_id: str
    profile: _PolicyProfile
    rich_qualification: list[_RichQualification]
    schema_version: Literal["metadata-policy-notebook-v1"] = (
        "metadata-policy-notebook-v1"
    )


class _RichFieldPreviewResponse(_ClosedModel):
    current_value: object
    desired_value: object | None
    field: str
    narrowed_for_target: bool
    option: str
    outcome: str
    qualified_value: object | None
    reason: str | None
    target_state: str


class _RichCasePreviewResponse(_ClosedModel):
    fields: list[_RichFieldPreviewResponse]
    proposal_set_id: str
    report_id: str
    resolved_policy_id: str
    work_label: str = Field(max_length=512)


class _PolicyImpactResponse(_ClosedModel):
    affected_categories: list[str]
    cases: list[_RichCasePreviewResponse]
    cases_total: int
    cases_truncated: bool
    current_unapplied_count: int
    library: str
    resolved_policy_id: str


class _PolicyPreviewResponse(_ClosedModel):
    active_policy_id: str
    libraries: list[_PolicyImpactResponse]
    policy_id: str
    rich_qualification: list[_RichQualification]
    schema_version: Literal["metadata-policy-preview-v1"] = "metadata-policy-preview-v1"


class _PolicyActivationRequest(_ClosedModel):
    policy_id: StrictStr = Field(pattern=r"^policy_[0-9a-f]{64}$")
    expected_active_policy_id: StrictStr = Field(pattern=r"^policy_[0-9a-f]{64}$")


class _PolicyTransitionResponse(_ClosedModel):
    transition_id: str
    from_policy_id: str
    to_policy_id: str
    generation: int
    state: str
    completed: int
    total: int
    failed: int
    acknowledged: bool
    origin: str
    schema_version: Literal["metadata-policy-transition-v1"] = (
        "metadata-policy-transition-v1"
    )


class _PolicyActivationResponse(_ClosedModel):
    policy_id: str
    transition: _PolicyTransitionResponse | None
    schema_version: Literal["metadata-policy-activation-v1"] = (
        "metadata-policy-activation-v1"
    )


class _ShadowPostureResponse(_ClosedModel):
    authority: Literal["none", "decision_only"]
    can_leave_preview: Literal[False]
    posture: Literal["review", "preview", "automatic"]
    schema_version: Literal["shadow-posture-v1"]


class _AutomaticSafetyResponse(_ClosedModel):
    paused: bool
    global_suspension: str | None
    category_suspensions: dict[str, str]


class _OperationalResponse(_ClosedModel):
    incidents: list["_OperationalIncident"]
    readiness: str
    schema_version: str
    summary: dict[str, object]
    timings: list[dict[str, object]]


class _OperationalIncident(_ClosedModel):
    settlement_code: Literal["persistence"] | None = None
    attempt: int | None = Field(default=None, ge=0, le=3)
    category: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    cause: StrictStr = Field(min_length=1, max_length=64)
    code: (
        Literal[
            "provider",
            "configuration",
            "authentication",
            "stale",
            "transient",
            "projection",
            "failed",
            "currentness",
            "persistence",
            "unsupported_media",
            "unreadable_media",
            "parser_refused",
            "multi_file_budget_exceeded",
            "missing_required_input",
            "malformed_retained_data",
            "internal_failure",
            "unknown",
        ]
        | None
    ) = None
    connection: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    count: int = Field(ge=1, le=10_000)
    diagnostic_reference: StrictStr | None = Field(
        default=None, max_length=21, pattern=r"^prep-[0-9a-f]{16}$"
    )
    field: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    incident_id: StrictStr = Field(min_length=1, max_length=96)
    kind: StrictStr
    library: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    scope: StrictStr
    stage: (
        Literal[
            "discovery", "extraction", "provider", "proposal", "admission", "projection"
        ]
        | None
    ) = None
    work_id: StrictStr | None = Field(default=None, min_length=1, max_length=512)


class _SupportPreviewResponse(_ClosedModel):
    bytes: int
    excluded: list[str]
    payload: dict[str, object]
    schema_version: str


class _ShadowLeaveImpact(_ClosedModel):
    affected_categories: list[str]
    current_unapplied_count: int
    library: str


class _ShadowLeaveResponse(_ClosedModel):
    authority: Literal["none"]
    can_confirm: Literal[False]
    confirmation_required: Literal[True]
    libraries: list[_ShadowLeaveImpact]
    posture: Literal["review", "preview"]
    requested_posture: Literal["qualified_decision_only_unavailable"]
    schema_version: Literal["shadow-posture-leave-preview-v1"]


class _ShadowFieldOutcomeResponse(_ClosedModel):
    action_id: str
    candidate_id: str | None
    citation_ids: list[str]
    combination_ids: list[str]
    destination: str
    disposition: Literal[
        "abstention",
        "candidate",
        "conflict",
        "ignore",
        "keep_current",
        "manual",
        "unsupported",
    ]
    outcome_id: str
    policy_category: str | None
    policy_destination: Literal["ask", "automatic", "ignore", "unsupported"]
    policy_option: str | None
    proposal_id: str
    qualification_candidate_id: str
    reason_code: str
    rule_id: str


class _ShadowOutcomeSetResponse(_ClosedModel):
    authority: Literal["none"]
    catalogue_sha256: str
    decision_policy_sha256: str
    decision_policy_version: str
    evaluated_at: int
    outcome_set_id: str
    outcomes: list[_ShadowFieldOutcomeResponse]
    policy_generation: int
    policy_profile_id: str
    posture: Literal["preview"]
    proposal_set_id: str
    report_id: str
    resolved_policy_id: str
    review_case_id: str
    schema_version: Literal["shadow-outcome-set-v7"]
    source_decision_set_id: str
    target_fingerprint: str


class _ShadowSampleResponse(_ClosedModel):
    authority: Literal["none"]
    items: list[_ShadowOutcomeSetResponse]
    next_cursor: None
    schema_version: Literal["shadow-outcome-sample-v1"]


class _PolicyTransitionFields(TypedDict):
    """The exact closed field set carried from one PolicyTransition."""

    transition_id: str
    from_policy_id: str
    to_policy_id: str
    generation: int
    state: str
    completed: int
    total: int
    failed: int
    acknowledged: bool
    origin: str


def _policy_transition_response(value: PolicyTransition) -> _PolicyTransitionResponse:
    return _PolicyTransitionResponse(
        **cast(
            "_PolicyTransitionFields",
            {
                "transition_id": value.transition_id,
                "from_policy_id": value.from_policy_id,
                "to_policy_id": value.to_policy_id,
                "generation": value.generation,
                "state": value.state,
                "completed": value.completed,
                "total": value.total,
                "failed": value.failed,
                "acknowledged": value.acknowledged,
                "origin": value.origin,
            },
        )
    )


def _policy_application_http_error(
    error: MetadataPolicyApplicationError,
) -> HTTPException:
    code = str(error)
    status = (
        409
        if "stale" in code or "processing" in code
        else (422 if "invalid" in code or "unqualified" in code else 503)
    )
    return HTTPException(status, detail={"code": code.replace(" ", "_")})


def _preparation_http_error(
    error: RuntimeError | StopIteration, *, retry: bool = False
) -> HTTPException:
    """Preserve closed preparation controls; collapse unexpected failures."""
    code = getattr(error, "code", "")
    if code == "unavailable":
        return HTTPException(503, detail={"code": code})
    if code == "library":
        return HTTPException(404, detail={"code": code})
    if code in {"paused", "stale", "blocked"} or (retry and code == "retry"):
        return HTTPException(409, detail={"code": code})
    return HTTPException(503, detail={"code": "persistence"})


def _policy_problem(error: MetadataPolicyError) -> str:
    """Translate closed-domain failures without reflecting input values."""
    return {
        "policy option is unavailable": "policy_option_unavailable",
        "policy destination is unavailable": "policy_destination_unavailable",
        "explicit policy is unsupported": "policy_explicit_unsupported",
    }.get(str(error), "policy_validation")


class BodyLimitMiddleware:
    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        maximum_bytes: int,
        decision_maximum_bytes: int,
        path_limits: dict[str, int] | None = None,
        hsts: bool = True,
    ) -> None:
        self._hsts = hsts
        self._app = app
        self._maximum_bytes = maximum_bytes
        self._decision_maximum_bytes = decision_maximum_bytes
        self._path_limits = {} if path_limits is None else dict(path_limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        is_login = path == "/api/v1/session/login"
        is_decision = bool(
            re.fullmatch(
                r"/api/v1/review-cases/[^/]+/decisions",
                path,
            )
        )
        is_policy_draft = path in {
            "/api/v1/metadata-policy/activate",
            "/api/v1/metadata-policy/drafts",
            "/api/v1/metadata-policy/validate",
            "/api/v1/metadata-policy/preview",
        }
        is_settings = path == "/api/v1/settings"
        configured_maximum = self._path_limits.get(path)
        if scope["type"] != "http" or not (
            configured_maximum is not None
            or is_login
            or is_decision
            or is_policy_draft
            or is_settings
        ):
            await self._app(scope, receive, send)
            return
        maximum = (
            configured_maximum
            if configured_maximum is not None
            else (
                self._maximum_bytes
                if is_login
                else (
                    (262_144 if is_settings else 16_384)
                    if is_policy_draft or is_settings
                    else self._decision_maximum_bytes
                )
            )
        )
        declared = next(
            (
                value
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )
        if declared is not None:
            try:
                if int(declared) > maximum:
                    await _oversized_body_response(
                        scope, receive, send, hsts=self._hsts
                    )
                    return
            except ValueError:
                await _oversized_body_response(scope, receive, send, hsts=self._hsts)
                return
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                await self._app(scope, _replay(messages), send)
                return
            size += len(message.get("body", b""))
            if size > maximum:
                await _oversized_body_response(scope, receive, send, hsts=self._hsts)
                return
            if not message.get("more_body", False):
                break
        await self._app(scope, _replay(messages), send)


def _replay(messages: list[Message]) -> Receive:
    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return receive


async def _oversized_body_response(
    scope: Scope, receive: Receive, send: Send, *, hsts: bool
) -> None:
    response = _error(413, "body_too_large")
    _set_security_headers(response, hsts=hsts)
    await response(scope, receive, send)


def create_review_app(
    application: ReviewApplication,
    configuration: ReviewWebConfiguration,
) -> FastAPI:
    sessions = application
    origin = urlsplit(configuration.canonical_origin)
    assets = ReactAssetLoader()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        application.start()
        try:
            yield
        finally:
            application.stop()

    app = FastAPI(
        title="Mechanical Librarian Review API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    session_cookie = APIKeyCookie(
        name=_COOKIE_NAME,
        scheme_name="OwnerSession",
        auto_error=False,
    )

    def authenticated_session_cookie(
        request: Request,
        ml_session: str | None = Security(session_cookie),
    ) -> str:
        _require_session(sessions, ml_session)
        assert ml_session is not None
        if (
            request.scope["path"].startswith("/api/v1/")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.scope["path"] != "/api/v1/session/login"
        ):
            bundle = assets.load()
            if isinstance(bundle, ReactAssetRecovery):
                raise HTTPException(503, detail={"code": bundle.code})
            if request.headers.get("x-ml-build-identity") != bundle.build_identity:
                raise HTTPException(409, detail={"code": "browser_build_mismatch"})
        return ml_session

    def browser_csrf(session_token: str) -> str:
        try:
            return sessions.issue_browser_csrf(session_token)
        except ReviewWebError as error:
            status_code = {"unauthorized": 401, "persistence": 503}.get(error.code, 503)
            raise HTTPException(status_code, detail={"code": error.code}) from None

    app.add_middleware(
        BodyLimitMiddleware,
        maximum_bytes=2048,
        decision_maximum_bytes=8192,
        hsts=origin.scheme == "https",
        path_limits={
            "/api/v1/review-preparation/dismiss": 2048,
            "/api/v1/reviewer-access/openrouter/refresh": 2048,
            # The write contract permits a 2,048-character key plus its
            # model, revision, and JSON framing.
            "/api/v1/reviewer-access/openrouter": 4096,
            "/api/v1/reviewer-access/description-model/test": 4096,
            "/api/v1/reviewer-access/selection": 2048,
            "/api/v1/reviewer-access/codex/device": 2048,
            "/api/v1/reviewer-access/codex/recheck": 2048,
            "/api/v1/reviewer-access/codex/device/cancel": 2048,
            "/api/v1/reviewer-access/codex": 2048,
        },
    )

    @app.middleware("http")
    async def trust_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Every path-based decision in this module reads the ASGI scope, never
        # `request.url.path`: the latter is rebuilt from the `Host` header, so a
        # header carrying a path and a `#` terminator reads as `/health/live` on
        # a request the router sent to a mutating API route
        # (GHSA-86qp-5c8j-p5mr). Routing itself dispatches on the scope path.
        if request.scope["path"] not in {"/health/live", "/health/ready"}:
            if not _request_matches_origin(
                request,
                configuration.canonical_origin,
                configuration.trusted_proxy_ips,
                require_forwarding=origin.scheme == "https"
                and configuration.tls_certificate_file is None,
            ):
                return _secured_error(
                    400, "invalid_origin", hsts=origin.scheme == "https"
                )
            if (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and request.headers.get("origin") != configuration.canonical_origin
            ):
                return _secured_error(403, "origin", hsts=origin.scheme == "https")
        # This hint changes scheduling only. Route authentication and CSRF still
        # run before application admission; mutations always remain foreground.
        with application.classify_request(
            "background"
            if request.method == "GET"
            and request.scope["path"].startswith("/api/v1/")
            and request.headers.get("x-ml-work-class") == "background"
            else "foreground"
        ):
            response = await call_next(request)
        _set_security_headers(response, hsts=origin.scheme == "https")
        return response

    @app.get("/health/live", include_in_schema=False)
    def liveness() -> PlainTextResponse:
        return PlainTextResponse("ok\n")

    @app.get("/health/ready", include_in_schema=False)
    def readiness() -> PlainTextResponse:
        ready = application.ready()
        return PlainTextResponse(
            "ready\n" if ready else "draining\n", status_code=200 if ready else 503
        )

    _register_session_and_operations_routes(
        app, application, configuration, authenticated_session_cookie
    )
    _register_settings_routes(app, application, authenticated_session_cookie)
    _register_reviewer_access_routes(app, application, authenticated_session_cookie)
    _register_policy_routes(app, application, authenticated_session_cookie)
    _register_case_and_effect_routes(
        app, application, session_cookie, authenticated_session_cookie
    )
    _register_browse_and_preparation_routes(
        app, application, session_cookie, authenticated_session_cookie
    )
    _register_live_update_route(app, application, configuration, session_cookie)
    _register_html_routes(app, application, session_cookie, browser_csrf, assets)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        detail: object = error.detail
        code = detail.get("code") if type(detail) is dict else "request"
        managed_by_file: Literal["/data/config.json"] | None = (
            "/data/config.json"
            if type(detail) is dict
            and detail.get("managed_by_file") == "/data/config.json"
            else None
        )
        return _error(error.status_code, str(code), managed_by_file)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if request.scope["path"].startswith("/api/v1/metadata-policy/"):
            if any(
                item.get("loc") == ("header", "x-csrf-token") for item in error.errors()
            ):
                return _error(403, "csrf")
            return _error(422, "policy_validation")
        return _error(422, "invalid")

    _document_build_identity_header(app)
    return app


def _document_build_identity_header(app: FastAPI) -> None:
    """Expose the runtime build gate on every authenticated unsafe operation."""
    generate = app.openapi

    def openapi() -> dict[str, object]:
        schema = generate()
        paths = schema.get("paths")
        if not isinstance(paths, dict):
            return schema
        for path, path_item in paths.items():
            if (
                not isinstance(path, str)
                or not path.startswith("/api/v1/")
                or path == "/api/v1/session/login"
                or not isinstance(path_item, dict)
            ):
                continue
            for method in ("post", "put", "patch", "delete"):
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                parameters = operation.setdefault("parameters", [])
                if isinstance(parameters, list) and not any(
                    isinstance(parameter, dict)
                    and parameter.get("name") == "x-ml-build-identity"
                    for parameter in parameters
                ):
                    parameters.append(
                        {
                            "description": (
                                "Exact identity of the installed authenticated "
                                "browser build."
                            ),
                            "in": "header",
                            "name": "x-ml-build-identity",
                            "required": True,
                            "schema": {
                                "pattern": "^[0-9a-f]{64}$",
                                "type": "string",
                            },
                        }
                    )
                responses = operation.setdefault("responses", {})
                if isinstance(responses, dict):
                    responses.setdefault(
                        "409",
                        {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/_ErrorResponse"
                                    }
                                }
                            },
                            "description": "Browser build mismatch",
                        },
                    )
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def _register_settings_routes(
    app: FastAPI,
    application: ReviewApplication,
    authenticated_session_cookie: _SessionDependency,
) -> None:
    @app.get(
        "/api/v1/appearance",
        response_model=_AppearanceResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def appearance(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _AppearanceResponse:
        _require_session(application, ml_session)
        if not application.settings_available:
            raise HTTPException(503, detail={"code": "settings_unavailable"})
        try:
            value = cast(SettingsView, application.owner_settings())
        except ReviewApplicationError:
            raise HTTPException(503, detail={"code": "settings_unavailable"}) from None
        return _AppearanceResponse(reduced_motion=value.document.reduced_motion)

    @app.get(
        "/api/v1/settings",
        response_model=SettingsView,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def settings(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> SettingsView:
        _require_session(application, ml_session)
        if not application.settings_available:
            raise HTTPException(503, detail={"code": "settings_unavailable"})
        try:
            return cast(SettingsView, application.owner_settings())
        except ReviewApplicationError:
            raise HTTPException(503, detail={"code": "settings_unavailable"}) from None

    @app.patch(
        "/api/v1/settings",
        response_model=SettingsView,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def write_settings(
        request: _SettingsWriteRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> SettingsView:
        _require_session(application, ml_session, x_csrf_token)
        if not application.settings_available:
            raise HTTPException(503, detail={"code": "settings_unavailable"})
        try:
            value = application.write_owner_settings(
                request.document, request.expected_revision, tuple(request.secrets)
            )
        except ReviewApplicationError as error:
            status = (
                409
                if error.code in {"settings_stale", "settings_locked"}
                else (422 if error.code == "settings_invalid" else 503)
            )
            detail = {
                "code": (
                    "settings_managed_by_file"
                    if error.code == "settings_locked"
                    else error.code
                )
            }
            if error.code == "settings_locked":
                detail["managed_by_file"] = "/data/config.json"
            raise HTTPException(status, detail=detail) from None
        return cast(SettingsView, value)


def reviewer_failure_response(error: Exception) -> tuple[int, str]:
    """Translate reviewer failures through one closed HTTP vocabulary."""
    if isinstance(error, ReviewerAccessRepositoryError):
        return (
            (409, "reviewer_access_stale")
            if str(error).endswith("stale")
            else (503, "reviewer_access_unavailable")
        )
    if isinstance(error, ReviewerAccessApplicationError):
        if error.code == "access_changed":
            return 409, "reviewer_access_stale"
        if error.code in {"credential_clear_failed", "unreachable"}:
            return 503, error.code
        return 422, error.code
    return 422, "reviewer_access_invalid"


def _register_reviewer_access_routes(
    app: FastAPI,
    application: ReviewApplication,
    authenticated_session_cookie: _SessionDependency,
) -> None:
    def authority() -> ReviewerAccessOperations:
        value = application.reviewer_access
        if value is None:
            raise HTTPException(503, detail={"code": "reviewer_access_unavailable"})
        return value

    def problem(error: Exception) -> HTTPException:
        status, code = reviewer_failure_response(error)
        return HTTPException(status, detail={"code": code})

    @app.get(
        "/api/v1/reviewer-access",
        response_model=ReviewerAccessView,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def reviewer_access(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> ReviewerAccessView:
        _require_session(application, ml_session)
        try:
            return authority().read()
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.post(
        "/api/v1/reviewer-access/openrouter/refresh",
        response_model=ReviewerAccessView,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def refresh_openrouter(
        task: Literal["cover_observation", "description_rewrite"] = "cover_observation",
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> ReviewerAccessView:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().refresh_openrouter(task)
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.post(
        "/api/v1/reviewer-access/description-model/contained",
        response_model=CodexDescriptionDisclosure,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)
        },
    )
    def describe_contained_description(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> CodexDescriptionDisclosure:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().describe_contained_description()
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.post(
        "/api/v1/reviewer-access/description-model/test",
        response_model=DescriptionModelMetadata,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def test_description_model(
        request: _OpenRouterWriteRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> DescriptionModelMetadata:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().test_description_model(
                request.api_key.get_secret_value(), request.model
            )
        except (
            ValueError,
            ReviewerAccessRepositoryError,
            ReviewerAccessApplicationError,
        ) as error:
            raise problem(error) from None

    @app.put(
        "/api/v1/reviewer-access/openrouter",
        response_model=ReviewerAccessView,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def write_openrouter(
        request: _OpenRouterWriteRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> ReviewerAccessView:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().configure_openrouter(
                expected_revision=request.expected_revision,
                api_key=request.api_key.get_secret_value(),
                model=request.model,
                task=request.task,
            )
        except (
            ValueError,
            ReviewerAccessRepositoryError,
            ReviewerAccessApplicationError,
        ) as error:
            raise problem(error) from None

    @app.delete(
        "/api/v1/reviewer-access/openrouter",
        response_model=ReviewerAccessView,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def clear_openrouter(
        request: _ReviewerRevisionRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> ReviewerAccessView:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().clear_openrouter(
                expected_revision=request.expected_revision
            )
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.put(
        "/api/v1/reviewer-access/selection",
        response_model=ReviewerAccessView,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def select_reviewer(
        request: _ReviewerSelectionRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> ReviewerAccessView:
        _require_session(application, ml_session, x_csrf_token)
        try:
            operations = authority()
            if request.engine == "codex":
                return operations.select_codex(
                    expected_revision=request.expected_revision, task=request.task
                )
            return operations.select_none(
                expected_revision=request.expected_revision, task=request.task
            )
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.post(
        "/api/v1/reviewer-access/codex/device",
        response_model=_CodexDeviceStartResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def start_codex_device(
        request: _ReviewerRevisionRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _CodexDeviceStartResponse:
        _require_session(application, ml_session, x_csrf_token)
        try:
            access, device = authority().start_codex(
                expected_revision=request.expected_revision
            )
            return _CodexDeviceStartResponse(
                access=access,
                device=_CodexDeviceAuthResponse(
                    state=device.state,
                    verification_url=device.verification_url,
                    user_code=device.user_code,
                    expires_in_seconds=device.expires_in_seconds,
                    failure=device.failure,
                ),
            )
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.get(
        "/api/v1/reviewer-access/codex/device",
        response_model=_CodexDeviceAuthResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def codex_device_status(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> CodexDeviceAuthState:
        _require_session(application, ml_session)
        try:
            return authority().codex_status()
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.post(
        "/api/v1/reviewer-access/codex/recheck",
        response_model=_CodexDeviceAuthResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def recheck_codex(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> CodexDeviceAuthState:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().recheck_codex()
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.post(
        "/api/v1/reviewer-access/codex/device/cancel",
        response_model=_CodexDeviceAuthResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def cancel_codex_device(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> CodexDeviceAuthState:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().cancel_codex()
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None

    @app.delete(
        "/api/v1/reviewer-access/codex",
        response_model=ReviewerAccessView,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def clear_codex(
        request: _ReviewerRevisionRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> ReviewerAccessView:
        _require_session(application, ml_session, x_csrf_token)
        try:
            return authority().clear_codex(expected_revision=request.expected_revision)
        except (ReviewerAccessRepositoryError, ReviewerAccessApplicationError) as error:
            raise problem(error) from None


def _register_session_and_operations_routes(
    app: FastAPI,
    application: ReviewApplication,
    configuration: ReviewWebConfiguration,
    authenticated_session_cookie: _SessionDependency,
) -> None:
    sessions = application

    @app.get(
        "/api/v1/operations/status",
        response_model=_OperationalResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def operations_status(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _OperationalResponse:
        _require_session(sessions, ml_session)
        if not application.operations_available:
            raise HTTPException(503, detail={"code": "operations_unavailable"})
        try:
            return _OperationalResponse.model_validate(application.operations_status())
        except (OperationsError, FieldEffectError):
            raise HTTPException(
                503, detail={"code": "operations_unavailable"}
            ) from None

    @app.get(
        "/api/v1/operations/support/preview",
        response_model=_SupportPreviewResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def support_preview(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _SupportPreviewResponse:
        _require_session(sessions, ml_session)
        try:
            if not application.operations_available:
                raise OperationsError("support_unavailable")
            return _SupportPreviewResponse.model_validate(application.support_preview())
        except OperationsError:
            raise HTTPException(503, detail={"code": "support_unavailable"}) from None

    @app.post(
        "/api/v1/operations/support/export",
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)
        },
    )
    def support_export(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            if not application.operations_available:
                raise OperationsError("support_unavailable")
            return Response(
                application.support_export(),
                media_type="application/json",
                headers={
                    "Content-Disposition": "attachment; filename=mechanical-librarian-support.json"
                },
            )
        except OperationsError:
            raise HTTPException(503, detail={"code": "support_unavailable"}) from None

    @app.get("/login", include_in_schema=False)
    def login_page() -> HTMLResponse:
        return HTMLResponse(review_frontend.login_html())

    @app.get("/assets/login.css", include_in_schema=False)
    def login_css() -> Response:
        return Response(review_frontend.LOGIN_CSS, media_type="text/css")

    @app.get("/assets/login.js", include_in_schema=False)
    def login_js() -> Response:
        return Response(review_frontend.LOGIN_JS, media_type="text/javascript")

    @app.post(
        "/api/v1/session/login",
        response_model=_SessionResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 413, 422, 429, 503)
        },
    )
    def login(payload: _LoginRequest, response: Response) -> _SessionResponse:
        try:
            tokens = sessions.login(payload.password)
        except ReviewWebError as error:
            status_code = {
                "invalid_credentials": 401,
                "persistence": 503,
                "throttled": 429,
            }.get(error.code, 500)
            raise HTTPException(status_code, detail={"code": error.code}) from None
        response.set_cookie(
            _COOKIE_NAME,
            tokens.session_token,
            httponly=True,
            secure=configuration.canonical_origin.startswith("https://"),
            samesite="strict",
            max_age=configuration.session_lifetime_seconds,
            path="/",
        )
        return _SessionResponse(
            actor="owner",
            csrf_token=tokens.csrf_token,
            expires_at=tokens.expires_at,
            schema_version="review-session-v1",
        )

    @app.post(
        "/api/v1/session/logout",
        status_code=204,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 403, 422, 503)
        },
    )
    def logout(
        response: Response,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        try:
            sessions.logout(ml_session, x_csrf_token)
        except ReviewWebError as error:
            status = {
                "csrf": 403,
                "persistence": 503,
                "unauthorized": 401,
            }.get(error.code, 503)
            raise HTTPException(status, detail={"code": error.code}) from None
        response.delete_cookie(
            _COOKIE_NAME,
            path="/",
            secure=configuration.canonical_origin.startswith("https://"),
            httponly=True,
            samesite="strict",
        )
        response.status_code = 204
        return response


def _register_policy_routes(
    app: FastAPI,
    application: ReviewApplication,
    authenticated_session_cookie: _SessionDependency,
) -> None:
    sessions = application
    notebook = application.policy
    automatic_safety = application.automatic_safety

    @app.post(
        "/api/v1/metadata-policy/drafts",
        response_model=_PolicyDraftResponse,
        status_code=201,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)
        },
    )
    def create_metadata_policy_draft(
        payload: _PolicyDraftRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PolicyDraftResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            profile = application.create_policy_draft(
                payload.name, payload.options, payload.destinations
            )
            policy_id = profile.policy_id
        except MetadataPolicyError as error:
            raise HTTPException(422, detail={"code": _policy_problem(error)}) from None
        except MetadataPolicyApplicationError as error:
            if str(error) == "policy automatic unqualified":
                raise _policy_application_http_error(error) from None
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except ReviewWebError:
            raise HTTPException(422, detail={"code": "policy_validation"}) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        return _PolicyDraftResponse(policy_id=policy_id)

    @app.get(
        "/api/v1/metadata-policy/state",
        response_model=_PolicyProfileResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def metadata_policy_state(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _PolicyProfileResponse:
        _require_session(sessions, ml_session)
        profile = notebook.active()
        return _PolicyProfileResponse(
            audiobook_qualification=[
                _AUDIOBOOK_QUALIFICATION_ADAPTER.validate_python(item)
                for item in notebook.audiobook_qualification(profile)
            ],
            policy_id=profile.policy_id,
            profile=_PolicyProfile.model_validate(profile.to_data()),
            rich_qualification=[
                _RICH_QUALIFICATION_ADAPTER.validate_python(item)
                for item in notebook.rich_qualification(profile)
            ],
        )

    @app.get(
        "/api/v1/metadata-policy/shadow",
        response_model=_ShadowPostureResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def shadow_posture(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _ShadowPostureResponse:
        _require_session(sessions, ml_session)
        try:
            return _ShadowPostureResponse.model_validate(notebook.shadow_state())
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/metadata-policy/shadow/enter",
        response_model=_ShadowPostureResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)
        },
    )
    def enter_shadow_preview(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _ShadowPostureResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _ShadowPostureResponse.model_validate(
                notebook.enter_preview(application.now())
            )
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/metadata-policy/shadow/confirm-automatic",
        response_model=_ShadowPostureResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)
        },
    )
    def confirm_automatic(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _ShadowPostureResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _ShadowPostureResponse.model_validate(
                notebook.confirm_automatic(application.now())
            )
        except MetadataPolicyApplicationError:
            raise HTTPException(422, detail={"code": "automatic_unavailable"}) from None

    def _automatic_safety_response() -> _AutomaticSafetyResponse:
        state = automatic_safety.state()
        return _AutomaticSafetyResponse(
            paused=state.paused,
            global_suspension=state.global_suspension,
            category_suspensions=dict(state.category_suspensions),
        )

    @app.get(
        "/api/v1/metadata-policy/automatic-safety",
        response_model=_AutomaticSafetyResponse,
    )
    def automatic_safety_state(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session)
        return _automatic_safety_response()

    @app.post(
        "/api/v1/metadata-policy/automatic-safety/pause",
        response_model=_AutomaticSafetyResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)
        },
    )
    def set_automatic_pause(
        payload: _AutomaticPauseRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            automatic_safety.pause(payload.paused, application.now())
            return _automatic_safety_response()
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except Exception:
            raise HTTPException(422, detail={"code": "request"}) from None

    @app.post(
        "/api/v1/metadata-policy/automatic-safety/recover",
        response_model=_AutomaticSafetyResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)
        },
    )
    def recover_automatic_safety(
        payload: _AutomaticRecoveryRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            category = payload.category
            if category is not None and not category:
                raise ValueError
            automatic_safety.recover(application.now(), category)
            return _automatic_safety_response()
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except Exception:
            raise HTTPException(422, detail={"code": "request"}) from None

    @app.post(
        "/api/v1/metadata-policy/automatic-safety/suspend",
        response_model=_AutomaticSafetyResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)
        },
    )
    def suspend_automatic_safety(
        payload: _AutomaticSuspendRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _AutomaticSafetyResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            if payload.category is not None and not payload.category:
                raise ValueError
            automatic_safety.suspend(
                payload.reason, application.now(), payload.category
            )
            return _automatic_safety_response()
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None
        except Exception:
            raise HTTPException(422, detail={"code": "request"}) from None

    @app.get(
        "/api/v1/metadata-policy/shadow/leave-preview",
        response_model=_ShadowLeaveResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def preview_leave_shadow(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _ShadowLeaveResponse:
        _require_session(sessions, ml_session)
        try:
            return _ShadowLeaveResponse.model_validate(notebook.preview_leave())
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.get(
        "/api/v1/metadata-policy/shadow/outcomes",
        response_model=_ShadowSampleResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 422, 503)},
    )
    def shadow_outcome_sample(
        limit: int = Query(default=20, ge=1, le=50),
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _ShadowSampleResponse:
        _require_session(sessions, ml_session)
        try:
            return _ShadowSampleResponse.model_validate(notebook.shadow_sample(limit))
        except MetadataPolicyApplicationError:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/metadata-policy/validate",
        response_model=_PolicyProfileResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)
        },
    )
    def validate_metadata_policy(
        payload: _PolicyDraftRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PolicyProfileResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            profile = application.validate_policy(
                payload.name, payload.options, payload.destinations
            )
        except MetadataPolicyError as error:
            raise HTTPException(422, detail={"code": _policy_problem(error)}) from None
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return _PolicyProfileResponse(
            audiobook_qualification=[
                _AUDIOBOOK_QUALIFICATION_ADAPTER.validate_python(item)
                for item in notebook.audiobook_qualification(profile)
            ],
            policy_id=profile.policy_id,
            profile=_PolicyProfile.model_validate(profile.to_data()),
            rich_qualification=[
                _RICH_QUALIFICATION_ADAPTER.validate_python(item)
                for item in notebook.rich_qualification(profile)
            ],
        )

    @app.get(
        "/api/v1/metadata-policy/drafts/{policy_id}",
        response_model=_PolicyProfileResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 404, 422, 503)
        },
    )
    def get_metadata_policy_draft(
        policy_id: str, ml_session: str = Depends(authenticated_session_cookie)
    ) -> _PolicyProfileResponse:
        _require_session(sessions, ml_session)
        try:
            profile = notebook.read(policy_id)
        except MetadataPolicyApplicationError:
            raise HTTPException(404, detail={"code": "unavailable"}) from None
        return _PolicyProfileResponse(
            audiobook_qualification=[
                _AUDIOBOOK_QUALIFICATION_ADAPTER.validate_python(item)
                for item in notebook.audiobook_qualification(profile)
            ],
            policy_id=profile.policy_id,
            profile=_PolicyProfile.model_validate(profile.to_data()),
            rich_qualification=[
                _RICH_QUALIFICATION_ADAPTER.validate_python(item)
                for item in notebook.rich_qualification(profile)
            ],
        )

    @app.post(
        "/api/v1/metadata-policy/preview",
        response_model=_PolicyPreviewResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 413, 422, 503)
        },
    )
    def preview_metadata_policy(
        payload: _PolicyDraftRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PolicyPreviewResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            profile, preview = application.preview_policy(
                payload.name, payload.options, payload.destinations
            )
        except MetadataPolicyError as error:
            raise HTTPException(422, detail={"code": _policy_problem(error)}) from None
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return _PolicyPreviewResponse(
            active_policy_id=preview.active_policy_id,
            policy_id=profile.policy_id,
            rich_qualification=[
                _RICH_QUALIFICATION_ADAPTER.validate_python(item)
                for item in notebook.rich_qualification(profile)
            ],
            libraries=[
                _PolicyImpactResponse(
                    affected_categories=list(item.affected_categories),
                    cases=[
                        _RichCasePreviewResponse.model_validate(
                            {**case.to_data(), "work_label": item.work_labels[index]}
                        )
                        for index, case in enumerate(item.cases)
                    ],
                    cases_total=item.cases_total,
                    cases_truncated=item.cases_truncated,
                    current_unapplied_count=item.current_unapplied_count,
                    library=item.library,
                    resolved_policy_id=item.resolved_policy_id,
                )
                for item in preview.libraries
            ],
        )

    @app.post(
        "/api/v1/metadata-policy/activate",
        response_model=_PolicyActivationResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def activate_metadata_policy(
        payload: _PolicyActivationRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PolicyActivationResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            result = notebook.activate(
                payload.policy_id,
                payload.expected_active_policy_id,
                application.now(),
            )
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return _PolicyActivationResponse(
            policy_id=result.active.policy_id,
            transition=None
            if result.transition is None
            else _policy_transition_response(result.transition),
        )

    @app.get(
        "/api/v1/metadata-policy/transition",
        response_model=_PolicyTransitionResponse | None,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def metadata_policy_transition(
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _PolicyTransitionResponse | None:
        try:
            transition = notebook.transition()
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None
        return None if transition is None else _policy_transition_response(transition)

    @app.post(
        "/api/v1/metadata-policy/transitions/{transition_id}/retry",
        response_model=_PolicyTransitionResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)
        },
    )
    def retry_metadata_policy_transition(
        transition_id: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PolicyTransitionResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _policy_transition_response(
                notebook.retry(transition_id, application.now())
            )
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None

    @app.post(
        "/api/v1/metadata-policy/transitions/{transition_id}/acknowledge",
        response_model=_PolicyTransitionResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)
        },
    )
    def acknowledge_metadata_policy_transition(
        transition_id: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PolicyTransitionResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _policy_transition_response(
                notebook.acknowledge(transition_id, application.now())
            )
        except MetadataPolicyApplicationError as error:
            raise _policy_application_http_error(error) from None


def _register_case_and_effect_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    authenticated_session_cookie: _SessionDependency,
) -> None:
    sessions = application

    @app.get(
        "/api/v1/review-cases/{review_case_id}",
        response_model=_CaseDetailResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 404, 409, 422)
        },
    )
    def case_detail(
        review_case_id: str,
        ml_session: str | None = Security(session_cookie),
    ) -> _CaseDetailResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        detail = _case_call(lambda: application.case_detail(review_case_id))
        return _CaseDetailResponse.model_validate(detail.to_data())

    @app.get(
        "/api/v1/review-cases/{review_case_id}/successor",
        response_model=_CaseSuccessorResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 404, 409, 422)
        },
    )
    def case_successor(
        review_case_id: str,
        ml_session: str | None = Security(session_cookie),
    ) -> _CaseSuccessorResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        return _CaseSuccessorResponse.model_validate(
            {
                "review_case_id": _case_call(
                    lambda: application.case_successor(review_case_id)
                )
            }
        )

    @app.get(
        "/api/v1/review-cases/{review_case_id}/cover-considerations/{consideration_id}/images/{reference}",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "image/png": {"schema": {"type": "string", "format": "binary"}}
                }
            },
            **{
                status: {"model": _ErrorResponse}
                for status in (401, 404, 409, 422, 503)
            },
        },
    )
    def cover_consideration_image(
        review_case_id: str,
        consideration_id: str,
        reference: str,
        ml_session: str | None = Security(session_cookie),
    ) -> Response:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        if (
            re.fullmatch(r"cover_consideration_[0-9a-f]{64}", consideration_id) is None
            or re.fullmatch(r"cover_ref_[0-9a-f]{64}", reference) is None
        ):
            raise HTTPException(404, detail={"code": "not_found"})
        payload = _case_call(
            lambda: application.cover_image(review_case_id, consideration_id, reference)
        )
        return Response(
            content=payload,
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post(
        "/api/v1/review-cases/{review_case_id}/cover-considerations/retry",
        response_model=_CoverConsiderationResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 404, 409, 422, 503)
        },
    )
    def retry_cover_consideration(
        review_case_id: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _CoverConsiderationResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        return _CoverConsiderationResponse.model_validate(
            _case_call(lambda: application.retry_cover_consideration(review_case_id))
        )

    @app.post(
        "/api/v1/review-cases/{review_case_id}/description-advice/retry",
        response_model=_DescriptionRetryAcknowledgement,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 404, 409, 422, 503)
        },
    )
    def retry_description_advice(
        review_case_id: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _DescriptionRetryAcknowledgement:
        _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        return _DescriptionRetryAcknowledgement.model_validate(
            _case_call(lambda: application.retry_description_advice(review_case_id))
        )

    @app.post(
        "/api/v1/review-cases/{review_case_id}/description-advice/selection",
        response_model=_DescriptionSelectionAcknowledgement,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 404, 409, 413, 422, 503)
        },
    )
    def select_description_advice(
        review_case_id: str,
        payload: _DescriptionSelectionRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
        idempotency_key: str = Header(min_length=16, max_length=128),
    ) -> _DescriptionSelectionAcknowledgement:
        _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        return _DescriptionSelectionAcknowledgement.model_validate(
            _decision_call(
                lambda: application.select_description_advice(
                    review_case_id,
                    payload.candidate_id,
                    payload.binding,
                    idempotency_key,
                )
            )
        )

    @app.post(
        "/api/v1/review-cases/{review_case_id}/decisions",
        response_model=_DecisionAcknowledgement,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 404, 409, 413, 422, 503)
        },
    )
    def record_decision(
        review_case_id: str,
        payload: _DecisionRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
        idempotency_key: str = Header(min_length=16, max_length=128),
    ) -> _DecisionAcknowledgement:
        actor = _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        acknowledgement = _DecisionAcknowledgement.model_validate(
            _decision_call(
                lambda: application.record_decision(
                    review_case_id,
                    proposal_id=payload.proposal_id,
                    target_fingerprint=payload.target_fingerprint,
                    destination=payload.destination,
                    actor=actor,
                    outcome=payload.outcome,
                    selected_value=(
                        None
                        if payload.selected_value is None
                        else payload.selected_value.model_dump(mode="json")
                    ),
                    idempotency_key=idempotency_key,
                    consideration_id=payload.consideration_id,
                )
            )
        )
        return acknowledgement

    def field_effect_call(callable_: Callable[[], _Result]) -> _Result:
        try:
            return callable_()
        except (GuardedApplicationError, FieldEffectError, RuntimeError) as error:
            code = getattr(error, "code", "unavailable")
            status_code = (
                404
                if code == "not_found"
                else 409
                if code in _FIELD_EFFECT_STATE_CONFLICT
                else 503
            )
            raise HTTPException(status_code, detail={"code": code}) from None

    @app.get(
        "/api/v1/activity",
        response_model=_ActivityResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 422, 503)},
    )
    def activity_status(
        q: str | None = Query(default=None),
        event_type: Literal[
            "all", "review", "policy", "metadata", "safety", "unknown"
        ] = Query(default="all"),
        range_days: int = Query(default=30, json_schema_extra={"enum": [7, 30, 90]}),
        page: int = Query(default=1, ge=1, le=1_000_000),
        page_size: int = Query(default=25, json_schema_extra={"enum": [25, 50, 100]}),
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> _ActivityResponse:
        _require_session(sessions, ml_session)
        # Query normalisation belongs at the HTTP boundary so hostile values
        # are a client error, never an application/persistence outage.  Both
        # the received and Unicode-casefolded forms are bounded.
        if q is not None:
            if len(q) > 128:
                raise HTTPException(status_code=422, detail={"code": "invalid"})
            q = q.strip().casefold() or None
            if q is not None and len(q) > 128:
                raise HTTPException(status_code=422, detail={"code": "invalid"})
        if range_days not in {7, 30, 90} or page_size not in {25, 50, 100}:
            raise HTTPException(status_code=422, detail={"code": "invalid"})
        return _ActivityResponse.model_validate(
            field_effect_call(
                lambda: application.activity(
                    search=q,
                    event_type=event_type,
                    range_days=range_days,
                    page=page,
                    page_size=page_size,
                )
            )
        )

    @app.get(
        "/api/v1/activity/{event_id}/transitions",
        response_model=_ActivityTransitions,
        responses={status: {"model": _ErrorResponse} for status in (401, 422, 503)},
    )
    def activity_transitions(
        event_id: str,
        after_sequence: int | None = Query(default=None, ge=1),
        ml_session: str = Depends(authenticated_session_cookie),
    ) -> dict[str, object]:
        _require_session(sessions, ml_session)
        if not valid_activity_transition_id(event_id):
            raise HTTPException(status_code=422, detail={"code": "invalid"})
        return field_effect_call(
            lambda: application.activity_transitions(event_id, after_sequence)
        )

    @app.post(
        "/api/v1/activity/consent",
        response_model=_ActivityResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)
        },
    )
    def enable_guarded_activity(
        payload: _ActivityConsentRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _ActivityResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        return _ActivityResponse.model_validate(
            field_effect_call(
                lambda: application.enable_activity(
                    tuple(
                        (grant.library, tuple(grant.destinations))
                        for grant in payload.grants
                    )
                )
            )
        )

    @app.post(
        "/api/v1/activity/{connection}/{library}/revoke",
        response_model=_ActivityResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)
        },
    )
    def revoke_guarded_activity(
        connection: str,
        library: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _ActivityResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        return _ActivityResponse.model_validate(
            field_effect_call(lambda: application.revoke_activity(connection, library))
        )

    @app.post(
        "/api/v1/review-cases/{review_case_id}/fields/{destination}/return-to-automatic",
        status_code=204,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 404, 409, 422, 503)
        },
    )
    def return_field_to_automatic(
        review_case_id: str,
        destination: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        _require_case_id(review_case_id)
        if destination not in SUPPORTED_DESTINATIONS:
            raise HTTPException(404, detail={"code": "not_found"})
        field_effect_call(
            lambda: application.return_to_automatic(review_case_id, destination)
        )
        return Response(status_code=204)

    @app.get(
        "/api/v1/field-effects/{plan_id}",
        response_model=dict[str, object],
        responses={
            status: {"model": _ErrorResponse} for status in (401, 404, 422, 503)
        },
    )
    def field_effect_status(
        plan_id: str, ml_session: str = Depends(authenticated_session_cookie)
    ) -> dict[str, object]:
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        return field_effect_call(lambda: application.field_effect_status(plan_id))

    @app.post(
        "/api/v1/field-effects/{plan_id}/apply",
        response_model=dict[str, object],
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 404, 409, 422, 503)
        },
    )
    def apply_field_effect(
        plan_id: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> dict[str, object]:
        _require_session(sessions, ml_session, x_csrf_token)
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        return field_effect_call(lambda: application.apply_field_effect(plan_id))

    @app.post(
        "/api/v1/field-effects/{plan_id}/reconcile",
        response_model=dict[str, object],
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 404, 409, 422, 503)
        },
    )
    def reconcile_field_effect(
        plan_id: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> dict[str, object]:
        _require_session(sessions, ml_session, x_csrf_token)
        if not re.fullmatch(r"effect_plan_[0-9a-f]{64}", plan_id):
            raise HTTPException(404, detail={"code": "not_found"})
        return field_effect_call(lambda: application.reconcile_field_effect(plan_id))

    @app.post(
        "/api/v1/field-effects/pause",
        status_code=204,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)
        },
    )
    def pause_field_effects(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        field_effect_call(application.pause_field_effects)
        return Response(status_code=204)

    @app.post(
        "/api/v1/field-effects/resume",
        status_code=204,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)
        },
    )
    def resume_field_effects(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> Response:
        _require_session(sessions, ml_session, x_csrf_token)
        field_effect_call(application.resume_field_effects)
        return Response(status_code=204)

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def openapi_document(
        ml_session: str | None = Security(session_cookie),
    ) -> JSONResponse:
        _require_session(sessions, ml_session)
        return JSONResponse(app.openapi())


class _BrowseDisconnected(Exception):
    """The caller left before its queued collection read acquired ownership."""


async def _browse_response(
    request: Request, read: Callable[[Callable[[], None]], _Result]
) -> _Result | Response:
    disconnected = threading.Event()

    def check_active() -> None:
        if disconnected.is_set():
            raise _BrowseDisconnected

    async def watch_disconnect() -> None:
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                disconnected.set()
                return

    watcher = asyncio.create_task(watch_disconnect())
    worker = asyncio.create_task(run_in_threadpool(read, check_active))
    try:
        try:
            # Cancellation never releases a connection still used by its worker.
            return await asyncio.shield(worker)
        except _BrowseDisconnected:
            return Response(status_code=204)
        except asyncio.CancelledError:
            disconnected.set()
            await asyncio.gather(worker, return_exceptions=True)
            raise
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher


def _register_browse_and_preparation_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    authenticated_session_cookie: _SessionDependency,
) -> None:
    sessions = application

    @app.get(
        "/api/v1/library",
        response_model=_LibraryResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 422, 503)
        },
    )
    async def library(
        request: Request,
        lens: str = Query(default="needs_attention", max_length=32),
        q: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=25, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=128),
        ml_session: str | None = Security(session_cookie),
    ) -> _LibraryResponse | Response:
        def read(check_active: Callable[[], None]) -> _LibraryResponse:
            _require_session(sessions, ml_session)
            return _LibraryResponse.model_validate(
                _query_call(
                    lambda: application.library(
                        lens=lens,
                        search=q,
                        limit=limit,
                        cursor=cursor,
                        check_active=check_active,
                    )
                ).to_data()
            )

        return await _browse_response(request, read)

    @app.get(
        "/api/v1/library/report",
        response_model=_ReportDetailResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 404, 422, 503)
        },
    )
    def library_report(
        connection: str = Query(max_length=512),
        library: str = Query(max_length=512),
        item: str = Query(max_length=512),
        ml_session: str | None = Security(session_cookie),
    ) -> _ReportDetailResponse:
        """Read-only: the recorded evidence for a work with no review case.

        The identity is a query rather than path segments because an item id
        may contain a slash, which no path match survives: ASGI decodes the
        escape before the router sees it.
        """
        _require_session(sessions, ml_session)
        return _ReportDetailResponse.model_validate(
            _report_call(lambda: application.report_detail(connection, library, item))
        )

    @app.get(
        "/api/v1/attention",
        response_model=_AttentionResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 422, 503)
        },
    )
    async def attention(
        request: Request,
        q: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=25, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=128),
        ml_session: str | None = Security(session_cookie),
    ) -> _AttentionResponse | Response:
        def read(check_active: Callable[[], None]) -> _AttentionResponse:
            _require_session(sessions, ml_session)
            return _AttentionResponse.model_validate(
                _query_call(
                    lambda: application.attention(
                        search=q,
                        limit=limit,
                        cursor=cursor,
                        check_active=check_active,
                    )
                ).to_data()
            )

        return await _browse_response(request, read)

    @app.get(
        "/api/v1/review-preparation",
        response_model=_PreparationResponse,
        responses={status: {"model": _ErrorResponse} for status in (401, 503)},
    )
    def review_preparation_status(
        ml_session: str | None = Security(session_cookie),
    ) -> _PreparationResponse:
        _require_session(sessions, ml_session)
        try:
            return _PreparationResponse.model_validate(
                application.preparation().to_data()
            )
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/review-preparation/first-boot/acknowledge",
        response_model=_PreparationResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 409, 422, 503)
        },
    )
    def acknowledge_first_boot_preparation(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _PreparationResponse.model_validate(
                application.acknowledge_first_boot_preparation().to_data()
            )
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/review-preparation/dismiss",
        response_model=_PreparationResponse,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 409, 413, 422, 503)
        },
    )
    def dismiss_preparation(
        command: _PreparationDismissRequest,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        try:
            return _PreparationResponse.model_validate(
                application.dismiss_preparation(command.run_id).to_data()
            )
        except ReviewApplicationError as error:
            raise _preparation_http_error(error) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/review-preparation/{connection}/{library_name}/scan",
        response_model=_PreparationProgress,
        status_code=202,
        responses={
            status: {"model": _ErrorResponse}
            for status in (400, 401, 403, 404, 409, 422, 503)
        },
    )
    def request_review_scan(
        connection: str,
        library_name: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationProgress:
        _require_session(sessions, ml_session, x_csrf_token)
        if not 1 <= len(connection) <= 64 or not 1 <= len(library_name) <= 64:
            raise HTTPException(404, detail={"code": "library"})
        try:
            return _PreparationProgress.model_validate(
                application.request_review_scan(connection, library_name)
            )
        except (RuntimeError, StopIteration) as error:
            raise _preparation_http_error(error) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    def preparation_control(
        paused: bool, ml_session: str, csrf: str
    ) -> _PreparationResponse:
        _require_session(sessions, ml_session, csrf)
        try:
            return _PreparationResponse.model_validate(
                (
                    application.pause_review_preparation()
                    if paused
                    else application.resume_review_preparation()
                ).to_data()
            )
        except (RuntimeError, StopIteration) as error:
            raise _preparation_http_error(error) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/review-preparation/pause",
        response_model=_PreparationResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)
        },
    )
    def pause_review_preparation(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationResponse:
        return preparation_control(True, ml_session, x_csrf_token)

    @app.post(
        "/api/v1/review-preparation/resume",
        response_model=_PreparationResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 422, 503)
        },
    )
    def resume_review_preparation(
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationResponse:
        return preparation_control(False, ml_session, x_csrf_token)

    @app.post(
        "/api/v1/review-preparation/{connection}/check-connection",
        response_model=_PreparationResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (401, 403, 404, 422, 503)
        },
    )
    def check_review_preparation_connection(
        connection: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationResponse:
        _require_session(sessions, ml_session, x_csrf_token)
        if not 1 <= len(connection) <= 64:
            raise HTTPException(404, detail={"code": "connection"})
        try:
            return _PreparationResponse.model_validate(
                application.check_review_preparation_connection(connection).to_data()
            )
        except RuntimeError as error:
            raise _preparation_http_error(error) from None
        except Exception:
            raise HTTPException(503, detail={"code": "persistence"}) from None

    @app.post(
        "/api/v1/review-preparation/{connection}/{library_name}/retry",
        response_model=_PreparationProgress,
        status_code=202,
        responses={
            status: {"model": _ErrorResponse}
            for status in (401, 403, 404, 409, 422, 503)
        },
    )
    def retry_review_preparation(
        connection: str,
        library_name: str,
        ml_session: str = Depends(authenticated_session_cookie),
        x_csrf_token: str = Header(),
    ) -> _PreparationProgress:
        _require_session(sessions, ml_session, x_csrf_token)
        if not 1 <= len(connection) <= 64 or not 1 <= len(library_name) <= 64:
            raise HTTPException(404, detail={"code": "library"})
        try:
            return _PreparationProgress.model_validate(
                application.retry_review_scan(connection, library_name)
            )
        except (RuntimeError, StopIteration) as error:
            raise _preparation_http_error(error, retry=True) from None

    @app.get(
        "/api/v1/review-cases/{review_case_id}/evidence",
        response_model=_EvidenceResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 404, 409, 422)
        },
    )
    def case_evidence(
        review_case_id: str,
        limit: int = Query(default=50, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=128),
        ml_session: str | None = Security(session_cookie),
    ) -> _EvidenceResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        return _EvidenceResponse.model_validate(
            _case_call(
                lambda: application.evidence(
                    review_case_id,
                    limit=limit,
                    cursor=cursor,
                )
            )
        )

    @app.get(
        "/api/v1/review-cases/{review_case_id}/history",
        response_model=_HistoryResponse,
        responses={
            status: {"model": _ErrorResponse} for status in (400, 401, 404, 409, 422)
        },
    )
    def case_history(
        review_case_id: str,
        ml_session: str | None = Security(session_cookie),
    ) -> _HistoryResponse:
        _require_session(sessions, ml_session)
        _require_case_id(review_case_id)
        return _HistoryResponse.model_validate(
            _case_call(lambda: application.history(review_case_id))
        )


def _register_live_update_route(
    app: FastAPI,
    application: ReviewApplication,
    configuration: ReviewWebConfiguration,
    session_cookie: APIKeyCookie,
) -> None:
    """One authenticated multiplexed invalidation stream per tab.

    It is deliberately a single endpoint rather than one per surface: a browser
    holds few connections, and every surface here invalidates on the same total
    order, so a second stream would buy nothing and cost a second thing to
    authenticate, bound and shut down.

    Authentication is the session cookie every other read already uses --
    `EventSource` cannot set headers, and no new mechanism was needed for a GET.
    A cross-origin subscribe is refused here as well as by the shared trust
    boundary, because a stream is the one read whose response keeps arriving
    after the check that admitted it (#641).
    """
    sessions = application

    @app.get(
        "/api/v1/updates",
        # Published like every other `/api/v1` read. OpenAPI cannot describe the
        # event grammar of a `text/event-stream` body, so the contract states
        # the media type and the frame shape in prose; leaving the endpoint out
        # entirely would have hidden a route a client has to know exists.
        response_class=Response,
        responses={
            200: {
                "content": {"text/event-stream": {}},
                "description": (
                    "An ordered stream of invalidation events. Each frame "
                    "carries an `id` of `<epoch>-<sequence>`, an `event` naming "
                    "the topic, and a `data` object of exactly `sequence`, "
                    "`subject` and `topic`. An event says what changed, never "
                    "what it changed to: the reader refetches the authoritative "
                    "answer over this same API. Reconnection is the browser's "
                    "own, presenting the last identity as `Last-Event-ID`; an "
                    "identity from a previous process is answered with a "
                    "`resync` event rather than a position in this order."
                ),
            }
        },
        summary="Subscribe to ordered invalidation for this session",
        # Described rather than declared as a dependency: a `Header` parameter
        # would make this the only read whose contract carries a validation
        # error envelope, and the browser sets this header itself on its own
        # reconnect. An unreadable value is answered with a resync, not a 422.
        openapi_extra={
            "parameters": [
                {
                    "description": (
                        "The last frame identity this reader saw, presented by "
                        "the browser's own reconnect. An identity from another "
                        "process, or one past the retained order, is answered "
                        "with a `resync` event."
                    ),
                    "in": "header",
                    "name": "last-event-id",
                    "required": False,
                    "schema": {"maxLength": 64, "type": "string"},
                }
            ]
        },
    )
    async def live_updates(
        request: Request,
        ml_session: str | None = Security(session_cookie),
    ) -> Response:
        last_event_id = request.headers.get("last-event-id")
        _require_session(sessions, ml_session)
        assert ml_session is not None
        origin = request.headers.get("origin")
        if origin is not None and origin != configuration.canonical_origin:
            raise HTTPException(403, detail={"code": "origin"})
        channel = application.updates
        try:
            subscription = channel.subscribe(
                live_stream.last_event_position(last_event_id, channel.epoch)
            )
        except LiveUpdateCapacityError:
            raise HTTPException(503, detail={"code": "capacity"}) from None

        def still_authenticated() -> bool:
            try:
                sessions.authenticate(ml_session)
            except ReviewWebError:
                return False
            return True

        return _LiveUpdateStreamingResponse(
            live_stream.stream_frames(
                subscription,
                channel.epoch,
                still_authenticated=still_authenticated,
            ),
            subscription,
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )


def _register_html_routes(
    app: FastAPI,
    application: ReviewApplication,
    session_cookie: APIKeyCookie,
    browser_csrf: Callable[[str], str],
    asset_loader: ReactAssetLoader,
) -> None:
    sessions = application

    def shell_response(ml_session: str | None) -> _ShellResponse:
        try:
            _require_session(sessions, ml_session)
        except HTTPException as error:
            if error.status_code == 401:
                return RedirectResponse(url="/login", status_code=303)
            raise
        assert ml_session is not None
        bundle = asset_loader.load()
        if isinstance(bundle, ReactAssetRecovery):
            return _react_asset_recovery_response()
        try:
            return HTMLResponse(bundle.shell_html(browser_csrf(ml_session)))
        except (OSError, ReactAssetError):
            return _react_asset_recovery_response()

    @app.get("/", include_in_schema=False)
    def root_page() -> RedirectResponse:
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/review/{review_case_id}", include_in_schema=False)
    def review_page(
        review_case_id: str,
        ml_session: str | None = Cookie(default=None),
    ) -> Response:
        return shell_response(ml_session)

    @app.get("/library", include_in_schema=False)
    def library_page(
        ml_session: str | None = Cookie(default=None),
    ) -> Response:
        return shell_response(ml_session)

    @app.get("/attention", include_in_schema=False)
    def attention_page(
        ml_session: str | None = Cookie(default=None),
    ) -> Response:
        return shell_response(ml_session)

    @app.get("/settings", include_in_schema=False)
    def settings_page(
        ml_session: str | None = Cookie(default=None),
    ) -> Response:
        return shell_response(ml_session)

    @app.get("/policy", include_in_schema=False)
    def policy_page(
        ml_session: str | None = Cookie(default=None),
    ) -> Response:
        return shell_response(ml_session)

    @app.get("/activity", include_in_schema=False)
    def activity_page(
        ml_session: str | None = Cookie(default=None),
    ) -> Response:
        return shell_response(ml_session)

    @app.get("/assets/review-app/{asset_path:path}", include_in_schema=False)
    def react_asset(asset_path: str) -> Response:
        bundle = asset_loader.load()
        if isinstance(bundle, ReactAssetRecovery):
            return _react_asset_recovery_response()
        selected = next(
            (asset for asset in bundle.assets if asset.path == asset_path), None
        )
        if selected is None or selected.kind == "shell":
            raise HTTPException(404, detail={"code": "asset_not_found"})
        try:
            payload = bundle.read(asset_path)
        except (OSError, ReactAssetError):
            return _react_asset_recovery_response()
        media_type = "text/css" if selected.kind == "style" else "text/javascript"
        return Response(payload, media_type=media_type)


def _react_asset_recovery_response() -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Review workspace unavailable</title></head><body>"
        "<main><h1>Review workspace unavailable</h1>"
        "<p>Ask the owner to restart after rebuilding the installed assets.</p>"
        "</main></body></html>",
        status_code=503,
    )


def load_owner_password_binding(
    path: Path,
) -> tuple[Callable[[str], bool], Callable[[str], str]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not getattr(os, "O_NOFOLLOW", 0):
        raise ReviewWebError("password_hash")
    descriptor = os.open(path, flags)
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_mode & 0o077:
            raise ReviewWebError("password_hash")
        payload = os.read(descriptor, 1025)
    finally:
        os.close(descriptor)
    try:
        encoded = payload.decode("utf-8", "strict").strip()
        if not encoded or len(payload) > 1024 or "\n" in encoded or "\r" in encoded:
            raise ValueError
    except (UnicodeError, ValueError, InvalidHashError) as error:
        raise ReviewWebError("password_hash") from error

    return owner_password_binding(encoded)


def load_owner_password_verifier(path: Path) -> Callable[[str], bool]:
    return load_owner_password_binding(path)[0]


def owner_password_verifier(encoded: str) -> Callable[[str], bool]:
    return owner_password_binding(encoded)[0]


def owner_password_binding(
    encoded: str,
) -> tuple[Callable[[str], bool], Callable[[str], str]]:
    try:
        if not encoded.startswith("$argon2id$") or PasswordHasher().check_needs_rehash(
            encoded
        ):
            raise ValueError
    except (ValueError, InvalidHashError) as error:
        raise ReviewWebError("password_hash") from error

    def verify(password: str) -> bool:
        try:
            return PasswordHasher().verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False

    def session_digest(token: str) -> str:
        return hmac.new(
            encoded.encode("utf-8"),
            b"mechanical-librarian.owner-session.v1\0" + token.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    return verify, session_digest


def _request_matches_origin(
    request: Request,
    canonical_origin: str,
    trusted_proxy_ips: tuple[str, ...],
    *,
    require_forwarding: bool,
) -> bool:
    headers = request.headers
    if len(headers.getlist("host")) != 1 or headers.getlist("forwarded"):
        return False
    host = headers["host"]
    scheme = request.url.scheme
    forwarded_hosts = headers.getlist("x-forwarded-host")
    forwarded_protos = headers.getlist("x-forwarded-proto")
    if forwarded_hosts or forwarded_protos:
        peer = None if request.client is None else request.client.host
        if (
            peer not in trusted_proxy_ips
            or len(forwarded_hosts) != 1
            or len(forwarded_protos) != 1
            or "," in forwarded_hosts[0]
            or forwarded_protos[0] not in {"http", "https"}
        ):
            return False
        host, scheme = forwarded_hosts[0], forwarded_protos[0]
    elif require_forwarding:
        return False
    expected = urlsplit(canonical_origin)
    return scheme == expected.scheme and host == expected.netloc


def _require_session(
    sessions: ReviewApplication,
    session_token: str | None,
    csrf_token: str | None = None,
) -> str:
    if session_token is None:
        raise HTTPException(401, detail={"code": "unauthorized"})
    try:
        return sessions.authenticate(session_token, csrf_token)
    except ReviewWebError as error:
        status = 403 if error.code == "csrf" else 401
        raise HTTPException(status, detail={"code": error.code}) from None


def _require_case_id(value: str) -> None:
    if _CASE_ID.fullmatch(value) is None:
        raise HTTPException(404, detail={"code": "not_found"})


def _case_call(call: Callable[[], _Result]) -> _Result:
    try:
        return call()
    except (ReviewWebError, ReviewApplicationError) as error:
        status = {
            "request": 400,
            "stale": 409,
            "already_decided": 409,
        }.get(error.code, 404)
        raise HTTPException(status, detail={"code": error.code}) from None


def _query_call(call: Callable[[], _Result]) -> _Result:
    try:
        return call()
    except ReviewWebError as error:
        status = 400 if error.code == "request" else 503
        raise HTTPException(status, detail={"code": error.code}) from None


def _report_call(call: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return call()
    except ReviewWebError as error:
        status = {"request": 400, "missing": 404}.get(error.code, 503)
        raise HTTPException(status, detail={"code": error.code}) from None


def _decision_call(call: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return call()
    except (ReviewWebError, ReviewApplicationError) as error:
        status = {
            "stale": 409,
            "already_decided": 409,
            "idempotency_conflict": 409,
            "invalid": 422,
            "invalid_selection": 422,
            "invalid_choice": 422,
            "cover_consideration_required": 422,
            "not_found": 404,
            "unsupported": 422,
            "persistence": 503,
        }.get(error.code, 503)
        raise HTTPException(status, detail={"code": error.code}) from None


def _error(
    status_code: int,
    code: str,
    managed_by_file: Literal["/data/config.json"] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": code,
                **({"managed_by_file": managed_by_file} if managed_by_file else {}),
            },
            "schema_version": "review-error-v1",
        },
        status_code=status_code,
    )


def _secured_error(status_code: int, code: str, *, hsts: bool) -> JSONResponse:
    response = _error(status_code, code)
    _set_security_headers(response, hsts=hsts)
    return response


def _set_security_headers(response: Response, *, hsts: bool) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if hsts:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
