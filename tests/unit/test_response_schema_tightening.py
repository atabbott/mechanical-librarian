"""The response keywords the generated browser decoders enforce (#1670, #1671).

Each row pins one keyword of a stage-2b item in the committed OpenAPI document,
which `npm run generate` turns into the browser's decoders. An enum is compared
with the vocabulary of the server code that produces the value, never with
`http.ts`: the decoder differential pins the browser side. Each owner also
refuses a value outside its keyword at validation, which is what turns a
server value outside the set into a 500 rather than a document the browser
would refuse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, get_args, get_type_hints

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from mechanical_librarian.adapters.reviewers import codex_device_auth
from mechanical_librarian.application import (
    contained_reviewer_contract,
    cover_identity,
    review_web,
)
from mechanical_librarian.application.field_effects import (
    CapabilityObservation,
    TargetServiceUnavailable,
)
from mechanical_librarian.application.reviewer_access import (
    CodexDescriptionDisclosure,
    OpenRouterRecommendation,
    ReviewerAccessView,
    ReviewerEngineStatus,
)
from mechanical_librarian.application.reviewer_contracts import (
    OpenRouterAccountStatus,
)
from mechanical_librarian.domain import (
    activity_records,
    human_decision,
    review_case,
    shadow_outcome,
)
from mechanical_librarian.domain.audio_inspection import AudioEvidenceSurface
from mechanical_librarian.domain.cover_evidence import CoverSourceKind
from mechanical_librarian.domain.field_effect import SUPPORTED_DESTINATIONS
from mechanical_librarian.domain.metadata_report import (
    CLAIM_BASES,
    SUPPORTED_FILE_FORMATS,
    ClaimKind,
    FieldState,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome
from mechanical_librarian.domain.review_field_classification import (
    ReviewFieldClassificationCause,
)
from mechanical_librarian.domain.reviewer_access import _OPENROUTER_MODEL_ID
from mechanical_librarian.interfaces.web import app

SCHEMAS: dict[str, Any] = json.loads(
    Path("docs/reference/review-openapi-v1.json").read_text(encoding="utf-8")
)["components"]["schemas"]


def _strings(hint: object) -> frozenset[str]:
    """Every string literal a `Literal` hint admits, through unions."""
    args = get_args(hint)
    if not args:
        return frozenset()
    found = {value for value in args if type(value) is str}
    return frozenset(found).union(*(_strings(value) for value in args))


def _values(kind: type[Any]) -> frozenset[str]:
    return frozenset(member.value for member in kind)


TARGETS = _values(TargetDestination)
# A report's file format, or what a terminal row's catalogue entry says when no
# format was read (`adapters/persistence/review_sql.py`, `_terminal_library_sql`).
MEDIA = SUPPORTED_FILE_FORMATS | {"audio", "unknown"}
SUSPENSIONS = _strings(get_type_hints(TargetServiceUnavailable.__init__)["reason"])
# Producers that state these sets only inline, cited where each is built.
CAPABILITY_STATES = frozenset(  # ContinuousGuardedApplication._capability_readiness
    {"allowed", "partially_allowed", "connection_unavailable", "not_ready"}
)
FAILURE_STAGES = frozenset(  # the terminal row's `stage` column CHECK
    {"extraction", "provider", "proposal", "admission", "projection", "complete"}
)
AUTOMATIC_OUTCOMES = frozenset(  # AutomaticDecision.__post_init__
    {"candidate", "keep_current", "ignore"}
)
REVIEWER_STATUSES = frozenset(  # cover_consideration._consideration_value
    {"completed", "failed", "unavailable"}
)
CANDIDATE_STATES = frozenset(  # CoverConsiderationService._candidate_view
    {"acceptable", "available", "excluded", "recommended"}
)
SCAN_STATES = frozenset(  # "idle" when no scan, else the review_scans state
    {"idle", "queued", "discovering", "processing", "completed"}
    | {"completed_with_failures"}
)
INSTANT = {"minimum": 0, "maximum": 8_640_000_000_000}
# The reasons the cover consideration service admits (#1671 narrowed it to the
# browser's two; no production path reports the stale-model reason).
COVER_UNAVAILABLE = contained_reviewer_contract._COVER_PROFILE_CONTRACT_REASONS
# The only producer of a usable openrouter status's tier.
OPENROUTER_TIERS = _strings(get_type_hints(OpenRouterAccountStatus)["tier"])

# (item, component, JSON pointer in the component, keyword, value). An `enum`
# row's value is the producing vocabulary and compares as a set.
KEYWORDS: tuple[tuple[str, str, str, str, object], ...] = (
    (
        "S01",
        "_ActivityDestination",
        "/properties/state",
        "enum",
        {"not_ready"}
        | _strings(get_type_hints(CapabilityObservation)["category"])
        | SUSPENSIONS,
    ),
    ("S02", "_ActivityDestination", "/properties/destination", "enum", TARGETS),
    ("S03", "_ActivityCapability", "/properties/state", "enum", CAPABILITY_STATES),
    ("S04", "_ActivitySuspension", "/properties/reason", "enum", SUSPENSIONS),
    ("S05", "_ActivitySuspension", "/properties/updated_at", "minimum", 0),
    *(
        ("S06", "_ActivityPrepared", f"/properties/{name}", "minimum", 0)
        for name in ("automatic", "human", "total")
    ),
    ("S07", "_ActivityExample", "/properties/destination", "enum", TARGETS),
    ("S08", "_ActivityWaiting", "/properties/destination", "enum", TARGETS),
    (
        "S09",
        "_ActivityEvent",
        "/properties/event_type",
        "enum",
        set(activity_records._CATEGORIES),
    ),
    ("S10", "_ActivityEvent", "/properties/occurred_at/anyOf/0", "minimum", 0),
    (
        "S11",
        "_ActivityResponse",
        "/properties/counts/additionalProperties",
        "minimum",
        0,
    ),
    (
        "S12",
        "_ProviderRecoveryActivity",
        "/properties/dispatch_reasons",
        "uniqueItems",
        True,
    ),
    (
        "S13",
        "_ProviderRecoveryActivity",
        "/properties/failure_reasons",
        "uniqueItems",
        True,
    ),
    (
        "S14",
        "_LibraryResponse",
        "/properties/lens",
        "enum",
        set(review_web._LIBRARY_LENSES),
    ),
    ("S15", "_LibraryResponse", "/properties/page", "minimum", 0),
    *(
        ("S16", "_ReviewCounts", f"/properties/{name}", "minimum", 0)
        for name in (
            "all",
            "needs_attention",
            "no_attention_needed",
            "not_matched",
            "preparation_failed",
        )
    ),
    ("S17", "_LibraryWork", "/properties/media_kind", "enum", MEDIA),
    ("S18", "_LibraryWork", "/properties/attention_count", "minimum", 0),
    ("S19", "_PreparationFailure", "/properties/stage", "enum", FAILURE_STAGES),
    (
        "S20",
        "_PreparationFailure",
        "/properties/status",
        "enum",
        set(review_web._TERMINAL_PREPARATION_GUIDANCE),
    ),
    *(
        ("S21", "_PreparationFailure", f"/properties/{name}", "minimum", 0)
        for name in ("attempt", "recovery_attempt")
    ),
    ("S22", "_TargetField", "/properties/state", "enum", _values(TargetFieldState)),
    ("S23", "_TargetField", "/properties/destination", "enum", TARGETS),
    *(
        (
            "S24",
            "_RecordingIdentityBasis",
            f"/properties/{name}",
            "pattern",
            "^[^\\x00-\\x1f\\x7f]*[^\\x00-\\x20\\x7f   -     　﻿][^\\x00-\\x1f\\x7f]*$",
        )
        for name in ("provider", "candidate")
    ),
    (
        "S25",
        "_ReportEmbeddedCover",
        "/properties/surface/anyOf/0",
        "enum",
        _values(AudioEvidenceSurface),
    ),
    ("S26", "_ReportEvidence", "/properties/basis", "enum", CLAIM_BASES),
    ("S27", "_ReportEvidence", "/properties/kind", "enum", _values(ClaimKind)),
    ("S28", "_ReportEvidence", "/properties/state", "enum", _values(FieldState)),
    ("S29", "_ReportDetailResponse", "/properties/media_kind", "enum", MEDIA),
    (
        "S30",
        "_AttentionResponse",
        "/properties/schema_version",
        "const",
        "review-attention-v1",
    ),
    ("S31", "_AttentionResponse", "/properties/total", "minimum", 0),
    ("S32", "_AttentionItem", "/properties/destination", "enum", TARGETS),
    ("S33", "_AttentionItem", "/properties/media_kind", "enum", MEDIA),
    ("S34", "_AttentionItem", "/properties/work_attention_count", "minimum", 0),
    (
        "S35",
        "_ReviewField",
        "/properties/decision_actions",
        "anyOf",
        [
            {"maxItems": 0, "minItems": 0, "type": "array"},
            {
                "maxItems": 2,
                "minItems": 2,
                "prefixItems": [
                    {"const": "keep_current", "type": "string"},
                    {"const": "accept", "type": "string"},
                ],
                "type": "array",
            },
        ],
    ),
    ("S36", "_ReviewField", "/properties/destination", "enum", TARGETS),
    (
        "S37",
        "_ReviewField",
        "/properties/deterministic_disposition",
        "enum",
        _values(ProposalOutcome),
    ),
    (
        "S38",
        "_ReviewField",
        "/properties/effect_capability",
        "enum",
        {"allowed", "unavailable"} | SUSPENSIONS,
    ),
    ("S39", "_TargetIdentity", "/properties/media_kind", "enum", MEDIA),
    (
        "S40",
        "_DecisionProjection",
        "/properties/outcome",
        "enum",
        human_decision._OUTCOMES,
    ),
    (
        "S41",
        "_AutomaticDecisionProjection",
        "/properties/outcome",
        "enum",
        AUTOMATIC_OUTCOMES,
    ),
    ("S42", "_DescriptionAdviceResponse", "/properties/model", "maxLength", 256),
    (
        "S43",
        "_DescriptionAdviceResponse",
        "/properties/attempt_id",
        "pattern",
        "^description_attempt_[0-9a-f]{32}$",
    ),
    ("S44", "_EvidenceItem", "/properties/destinations/items", "enum", TARGETS),
    ("S45", "_EvidenceItem", "/properties/destinations", "uniqueItems", True),
    (
        "S46",
        "_ProviderOutcome",
        "/properties/lane_kind",
        "enum",
        _values(ProviderQueryKind),
    ),
    (
        "S47",
        "_ProviderOutcome",
        "/properties/status",
        "enum",
        _values(ProviderStatus) | {"adapter_fault"},
    ),
    ("S48", "_MediaHealthObservation", "/properties/tracks/items", "minimum", 0),
    (
        "S49",
        "_HistoryTransitionItem",
        "/properties/kind",
        "enum",
        review_case._TRANSITION_KIND,
    ),
    (
        "S50",
        "_HistoryPreviewItem",
        "/properties/dispositions/propertyNames",
        "enum",
        shadow_outcome._DISPOSITIONS,
    ),
    (
        "S51",
        "_HistoryPreviewItem",
        "/properties/dispositions/additionalProperties",
        "minimum",
        0,
    ),
    *(
        ("S52", component, "/properties/occurred_at", keyword, value)
        for component in (
            "_HistoryDecisionItem",
            "_HistoryPreviewItem",
            "_HistoryAutomaticItem",
            "_HistoryCoverItem",
            "_HistoryDescriptionItem",
        )
        for keyword, value in INSTANT.items()
    ),
    *(
        ("S53", component, "/properties/destination", "enum", TARGETS)
        for component in ("_HistoryDecisionItem", "_HistoryAutomaticItem")
    ),
    ("S54", "_HistoryDescriptionItem", "/properties/model", "maxLength", 256),
    ("S55", "_ProposedValue", "/properties/kind", "maxLength", 64),
    ("S56", "_ProposedValue", "/properties/value/anyOf/0", "maxLength", 16_384),
    ("S57", "_ProposedValue", "/properties/value/anyOf/2", "maxItems", 128),
    ("S57", "_ProposedValue", "/properties/value/anyOf/3", "maxItems", 128),
    ("S58", "_ProposedValue", "/properties/value/anyOf/2/items", "maxLength", 512),
    ("S59", "_SeriesValue", "/properties/name", "maxLength", 512),
    ("S60", "_SeriesValue", "/properties/sequence/anyOf/0", "maxLength", 512),
    (
        "S61",
        "_CoverConsiderationResponse",
        "/properties/reviewer_status",
        "enum",
        REVIEWER_STATUSES,
    ),
    (
        "S63",
        "_CoverConsiderationResponse",
        "/properties/reviewer_model/anyOf/0",
        "maxLength",
        256,
    ),
    (
        "S64",
        "_CoverConsiderationResponse",
        "/properties/reviewer_access_revision/anyOf/0",
        "minimum",
        0,
    ),
    (
        "S62",
        "_CoverConsiderationResponse",
        "/properties/reviewer_unavailable_reason/anyOf/0",
        "enum",
        COVER_UNAVAILABLE,
    ),
    ("S65", "_CoverPolicyReport", "/properties/reasons", "maxItems", 32),
    ("S65", "_CoverPolicyReport", "/properties/reasons/items", "maxLength", 128),
    (
        "S66",
        "_CoverPolicyReport",
        "/properties/acceptable_candidate_references",
        "uniqueItems",
        True,
    ),
    ("S67", "_CoverCandidate", "/properties/state", "enum", CANDIDATE_STATES),
    (
        "S68",
        "_CoverCandidate",
        "/properties/source_kinds/items",
        "enum",
        _values(CoverSourceKind),
    ),
    ("S69", "_CoverCandidate", "/properties/deterministic_rank/anyOf/0", "minimum", 0),
    *(
        ("S70", "_CoverCandidate", "/properties/deterministic_rank_key/anyOf/0", key, 7)
        for key in ("minItems", "maxItems")
    ),
    *(
        ("S71", "_CoverCandidate", f"/properties/{name}", "minimum", 0)
        for name in ("height", "ordinal", "width")
    ),
    ("S72", "_CoverIdentity", "/properties/outcome", "enum", cover_identity._OUTCOMES),
    ("S73", "_PreparationProgress", "/properties/state", "enum", SCAN_STATES),
    *(
        ("S74", "_PreparationProgress", f"/properties/{name}", "minimum", 0)
        for name in ("checked", "total", "ready", "blocked", "recorded", "not_matched")
    ),
    ("S75", "_PreparationActiveBook", "/properties/stage", "maxLength", 64),
    (
        "S76",
        "_CodexDeviceAuthResponse",
        "/properties/verification_url/anyOf/0",
        "const",
        codex_device_auth._DEVICE_URL,
    ),
    (
        "S77",
        "_CodexDeviceAuthResponse",
        "/properties/user_code/anyOf/0",
        "pattern",
        codex_device_auth._CODE.pattern,
    ),
    (
        "S78",
        "_CodexDeviceAuthResponse",
        "/properties/expires_in_seconds/anyOf/0",
        "const",
        900,
    ),
    # E, pinned: the acknowledgement already bounds its sequences.
    (
        "S79",
        "_DecisionAcknowledgement",
        "/properties/invalidation_sequences",
        "maxItems",
        8,
    ),
    # Application views (#1671), built per read and never stored.
    ("S81", "CodexDescriptionDisclosure", "/properties/model", "minLength", 1),
    (
        "S82",
        "CodexDescriptionDisclosure",
        "/properties/profile_sha256",
        "pattern",
        "^[0-9a-f]{64}$",
    ),
    (
        "S83",
        "ReviewerEngineStatus",
        "/properties/host_headroom_bytes/anyOf/0",
        "minimum",
        0,
    ),
    (
        "S84",
        "ReviewerEngineStatus",
        "/properties/tier/anyOf/0",
        "enum",
        OPENROUTER_TIERS,
    ),
    (
        "S85",
        "ReviewerAccessView",
        "/properties/recommendations/items",
        "pattern",
        _OPENROUTER_MODEL_ID.pattern,
    ),
    (
        "S86",
        "OpenRouterRecommendation",
        "/properties/model",
        "pattern",
        _OPENROUTER_MODEL_ID.pattern,
    ),
    (
        "S87",
        "OpenRouterRecommendation",
        "/properties/requests_per_minute/anyOf/0",
        "minimum",
        1,
    ),
    # The code Library keys a field's row on, never the sentence (#1791).
    (
        "S88",
        "_ReviewField",
        "/properties/classification_cause",
        "enum",
        _strings(ReviewFieldClassificationCause),
    ),
    # The condition a withheld reason states, never its sentence (#1802).
    (
        "S89",
        "_ReviewField",
        "/properties/automatic_withheld_condition/anyOf/0",
        "enum",
        _strings(review_web.AutomaticWithheldCondition),
    ),
)


def _at(component: str, pointer: str) -> dict[str, Any]:
    node = SCHEMAS[component]
    for part in pointer.split("/")[1:]:
        node = node[int(part)] if type(node) is list else node[part]
    return node


@pytest.mark.parametrize(
    ("component", "pointer", "keyword", "value"),
    [row[1:] for row in KEYWORDS],
    ids=[f"{row[0]}-{row[1]}{row[2]}-{row[3]}" for row in KEYWORDS],
)
def test_the_document_states_each_keyword(
    component: str, pointer: str, keyword: str, value: object
) -> None:
    stated = _at(component, pointer)[keyword]
    if keyword == "enum":
        assert len(stated) == len(set(stated))
        assert set(stated) == set(value)  # type: ignore[call-overload]
    else:
        assert stated == value


def test_the_cover_unavailable_reasons_are_the_two_resource_limits() -> None:
    # The service admits only the two limits the response and browser state; a
    # stale model is refused as profile_contract_mismatch, never reported.
    contract = contained_reviewer_contract
    assert COVER_UNAVAILABLE == {
        f"profile contract requires memory_bytes={contract.COVER_MEMORY_BYTES}",
        f"profile contract requires process_limit={contract.COVER_PROCESS_LIMIT}",
    }
    stale_model = f"profile contract requires model={contract.COVER_MODEL}"
    assert not contract.is_cover_profile_contract_reason(stale_model)
    assert all(map(contract.is_cover_profile_contract_reason, COVER_UNAVAILABLE))


def test_the_shared_reviewer_model_stays_untightened() -> None:
    # X43: the codex and openrouter slots need different model grammars.
    assert "pattern" not in _at("ReviewerEngineStatus", "/properties/model/anyOf/0")


def test_every_activity_destination_is_a_target_destination() -> None:
    # Activity reads only the destinations field effects support; its schema
    # states the whole target vocabulary, which the browser shares.
    assert SUPPORTED_DESTINATIONS <= TARGETS


# The recording reference pattern under pydantic's rust-regex engine, against
# the browser's rule: not blank after JavaScript `trim`, and no C0 or DEL.
_REFERENCE = TypeAdapter(
    Annotated[
        str,
        Field(
            pattern=_at("_RecordingIdentityBasis", "/properties/provider")["pattern"]
        ),
    ]
)


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("Audnexus", True),
        ("recording-1", True),
        (" padded ", True),
        ("Café", True),
        ("\u0085", True),  # JavaScript `trim` keeps NEL
        ("", False),
        (" ", False),
        (" 　", False),
        ("﻿", False),
        (" ", False),
        ("a\tb", False),
        ("bad\nsource", False),
        ("a\x7f", False),
        ("\x1f", False),
    ],
)
def test_the_recording_reference_pattern_reads_as_the_browser_does(
    value: str, accepted: bool
) -> None:
    try:
        _REFERENCE.validate_python(value)
    except ValidationError:
        assert not accepted
    else:
        assert accepted


# One value outside each owner's keyword: (owner, field, value, error type).
REFUSALS: tuple[tuple[type[BaseModel], str, object, str], ...] = (
    (app._ActivityDestination, "state", "writable", "literal_error"),
    (app._ActivityCapability, "state", "partial", "literal_error"),
    (app._ActivitySuspension, "reason", "recovery", "literal_error"),
    (app._ActivityPrepared, "total", -1, "greater_than_equal"),
    (app._ActivityExample, "destination", "publication_date", "literal_error"),
    (app._ActivityWaiting, "destination", "x", "literal_error"),
    (app._ActivityEvent, "event_type", "all", "literal_error"),
    (app._ActivityResponse, "counts", {"verified": -1}, "greater_than_equal"),
    (
        app._ProviderRecoveryActivity,
        "dispatch_reasons",
        ["blanket_scan", "blanket_scan"],
        "value_error",
    ),
    (
        app._ProviderRecoveryActivity,
        "failure_reasons",
        ["authentication", "authentication"],
        "value_error",
    ),
    (app._LibraryResponse, "lens", "unmatched", "literal_error"),
    (app._ReviewCounts, "all", -1, "greater_than_equal"),
    (app._LibraryWork, "media_kind", "ebook", "literal_error"),
    (app._PreparationFailure, "stage", "reading", "literal_error"),
    (app._TargetField, "state", "absent", "literal_error"),
    (app._RecordingIdentityBasis, "provider", "bad\nsource", "string_pattern_mismatch"),
    (app._ReportEmbeddedCover, "surface", "apic", "literal_error"),
    (app._ReportEvidence, "basis", "filename", "literal_error"),
    (app._ReportDetailResponse, "media_kind", "ebook", "literal_error"),
    (app._AttentionResponse, "schema_version", "review-attention-v2", "literal_error"),
    (app._AttentionItem, "work_attention_count", -1, "greater_than_equal"),
    (app._ReviewField, "decision_actions", ["accept"], "too_long"),
    (app._ReviewField, "classification_cause", "queued", "literal_error"),
    (
        app._ReviewField,
        "automatic_withheld_condition",
        "subtitle_segment_provider_dissent",
        "literal_error",
    ),
    (app._TargetIdentity, "media_kind", "ebook", "literal_error"),
    (app._DecisionProjection, "outcome", "approve", "literal_error"),
    (app._AutomaticDecisionProjection, "outcome", "accept", "literal_error"),
    (
        app._DescriptionAdviceResponse,
        "attempt_id",
        "attempt",
        "string_pattern_mismatch",
    ),
    (app._EvidenceItem, "destinations", ["asin", "asin"], "value_error"),
    (app._EvidenceItem, "language", "x" * 1025, "string_too_long"),
    (app._ProviderOutcome, "lane_kind", "identity", "literal_error"),
    (app._MediaHealthObservation, "tracks", [-1], "greater_than_equal"),
    (app._HistoryTransitionItem, "kind", "invented", "literal_error"),
    (app._HistoryPreviewItem, "dispositions", {"approve": 1}, "literal_error"),
    (app._HistoryDecisionItem, "occurred_at", 8_640_000_000_001, "less_than_equal"),
    (app._HistoryAutomaticItem, "destination", "cover_art", "literal_error"),
    (app._HistoryCoverItem, "occurred_at", -1, "greater_than_equal"),
    (app._HistoryDescriptionItem, "model", "m" * 257, "string_too_long"),
    (app._ProposedValue, "kind", "k" * 65, "string_too_long"),
    (app._SeriesValue, "name", "n" * 513, "string_too_long"),
    (app._CoverConsiderationResponse, "reviewer_status", "invented", "literal_error"),
    (
        app._CoverConsiderationResponse,
        "reviewer_unavailable_reason",
        "profile contract requires model=gpt-5.6-sol",
        "literal_error",
    ),
    (app._CoverPolicyReport, "reasons", ["r" * 129], "string_too_long"),
    (
        app._CoverPolicyReport,
        "acceptable_candidate_references",
        ["candidate-1", "candidate-1"],
        "value_error",
    ),
    (app._CoverCandidate, "deterministic_rank_key", [1, 2], "too_short"),
    (app._CoverIdentity, "outcome", "match", "literal_error"),
    (app._PreparationProgress, "state", "paused", "literal_error"),
    (app._PreparationActiveBook, "stage", "s" * 65, "string_too_long"),
    (app._CodexDeviceAuthResponse, "user_code", "ABC-1234", "string_pattern_mismatch"),
    (CodexDescriptionDisclosure, "model", "", "string_too_short"),
    (
        CodexDescriptionDisclosure,
        "profile_sha256",
        "A" * 64,
        "string_pattern_mismatch",
    ),
    (ReviewerEngineStatus, "host_headroom_bytes", -1, "greater_than_equal"),
    (ReviewerEngineStatus, "tier", "trial", "literal_error"),
    (
        ReviewerAccessView,
        "recommendations",
        ("provider/model\nBearer secret",),
        "string_pattern_mismatch",
    ),
    (OpenRouterRecommendation, "model", "gpt-6-luna", "string_pattern_mismatch"),
    (OpenRouterRecommendation, "requests_per_minute", 0, "greater_than_equal"),
)


@pytest.mark.parametrize(
    ("owner", "field", "value", "error"),
    REFUSALS,
    ids=[f"{row[0].__name__}.{row[1]}" for row in REFUSALS],
)
def test_each_owner_refuses_a_value_outside_its_keyword(
    owner: type[BaseModel], field: str, value: object, error: str
) -> None:
    with pytest.raises(ValidationError) as refused:
        owner.__pydantic_validator__.validate_assignment(
            owner.model_construct(), field, value
        )
    first = refused.value.errors()[0]
    assert (first["type"], first["loc"][0]) == (error, field)
