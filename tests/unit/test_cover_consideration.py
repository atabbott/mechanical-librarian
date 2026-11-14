from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, cast
from unittest.mock import patch

import pytest

from mechanical_librarian.application import cover_observation
from mechanical_librarian.application.cover_consideration import (
    SCHEMA_VERSION as COVER_CONSIDERATION_SCHEMA,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsideration,
    CoverConsiderationError,
    CoverConsiderationService,
    ReviewRunner,
    cover_consideration_currentness,
    cover_consideration_currentness_document,
    cover_consideration_is_current,
    cover_consideration_replay_digest,
    cover_currentness_from_facts,
)
from mechanical_librarian.application.cover_packet_semantics import (
    canonical_packet_json,
)
from mechanical_librarian.application.cover_packets import cover_source_reference
from mechanical_librarian.application.cover_policy import (
    POLICY_SCHEMA_VERSION as COVER_POLICY_SCHEMA,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.review_web import _needs_attention
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
)
from mechanical_librarian.application.reviewer_contracts import (
    OpenRouterFailure,
    ReviewerDispatch,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderCandidate,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import work_route_cover_sources
from mechanical_librarian.domain.reviewer_access import ReviewerEngine
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.support.audiobook_reports import asserted_asin_report
from tests.support.hardcover_work_route import (
    WORK,
    hardcover_work_report,
    work_cover_source,
)
from tests.support.metadata_report_schemas import retained_report_as_current
from tests.unit.test_cover_packets import (
    _audiobook_cover_source,
    _audiobook_report,
    _observer_result,
    _preview,
    _report,
    _source,
)


def test_dynamic_dispatch_binds_engine_model_profile_schema_and_candidate(
    case: _Case,
) -> None:
    profile = b"openrouter-profile"
    dispatches: list[str] = []

    def reviewer(*args: object) -> str:
        dispatches.append("openrouter")
        return _observer_result(
            *cast(
                "tuple[bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]]",
                args,
            )
        )

    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        reviewer_dispatch=lambda: ReviewerDispatch(
            7,
            "openrouter",
            "example/vision",
            profile,
            cast("ReviewRunner", reviewer),
        ),
        reviewer_identity=lambda: (7, "openrouter", "example/vision", profile),
        reviewer_revision_current=lambda revision: revision == 7,
    )
    case_id = "review_case_" + "9" * 64
    projected = service.ensure(case_id, case.report.to_json(), case.proposal)
    persisted = json.loads(case.store.current[case_id])
    observation = json.loads(cast("str", persisted["observation_document"]))

    assert dispatches == ["openrouter"]
    assert projected["reviewer_engine"] == "openrouter"
    assert projected["reviewer_model"] == "example/vision"
    assert persisted["reviewer_engine"] == "openrouter"
    assert persisted["reviewer_model"] == "example/vision"
    assert persisted["reviewer_access_revision"] == 7
    assert observation["observer_reference"] == cover_observation._observer_reference(
        profile,
        observation["instruction_sha256"],
        observation["output_schema_sha256"],
    )
    assert observation["output_schema_sha256"]
    assert {row["candidate_reference"] for row in observation["observations"]} == {
        candidate["reference"] for candidate in persisted["candidates"]
    }


def test_failed_dynamic_dispatch_preserves_current_owner_identity(case: _Case) -> None:
    identity = (7, "openrouter", "example/vision", b"openrouter-profile")

    def unavailable() -> ReviewerDispatch:
        raise ReviewerAccessApplicationError("unreachable", identity)

    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        reviewer_dispatch=unavailable,
        reviewer_identity=lambda: identity,
        reviewer_revision_current=lambda revision: revision == 7,
    )
    case_id = "review_case_" + "8" * 64
    request = service.prepare_observation(case_id, case.report.to_json())
    evidence = service.observe(request)
    projected = service.ensure(
        case_id,
        case.report.to_json(),
        case.proposal,
        reviewer_evidence=evidence,
    )

    assert projected["reviewer_status"] == "unavailable"
    assert projected["reviewer_engine"] == "openrouter"
    assert projected["reviewer_model"] == "example/vision"
    assert projected["reviewer_access_revision"] == 7


class _Store:
    def __init__(self) -> None:
        self.current: dict[str, str] = {}
        self.events: list[dict[str, object]] = []

    def current_cover_consideration(self, review_case_id: str, replay_digest: str):
        document = self.current.get(review_case_id)
        if document is None or json.loads(document)["replay_digest"] != replay_digest:
            return None
        return document

    def current_cover_consideration_document(self, review_case_id: str):
        return self.current.get(review_case_id)

    def latest_cover_consideration_document(self, review_case_id: str):
        return self.current.get(review_case_id)

    def record_cover_consideration(self, document: str, **values):
        review_case_id = values["review_case_id"]
        previous = self.current.get(review_case_id)
        if previous is not None:
            self.events.append(
                {"kind": "superseded", "occurred_at": values["occurred_at"]}
            )
        self.current[review_case_id] = document
        self.events.append(
            {
                "kind": values.get("preparation_kind", "automatic_prepared"),
                "occurred_at": values["occurred_at"],
            }
        )
        return document

    def record_cover_consideration_outcome(
        self, consideration_id, kind, occurred_at, decision_id=None
    ):
        self.events.append(
            {
                "consideration_id": consideration_id,
                "decision_id": decision_id,
                "kind": kind,
                "occurred_at": occurred_at,
            }
        )

    def cover_consideration_history(self, review_case_id, limit=50):
        del review_case_id
        return tuple(reversed(self.events[-limit:]))


class _Case:
    """The coupled service, store and clock a `setUp` used to carry."""

    def __init__(self) -> None:
        incumbent_payload = _preview(1)
        alternative_payload = _preview(2)
        self.sources = (
            _source(1, incumbent_payload, incumbent=True),
            _source(2, alternative_payload, embedded=True),
        )
        self.report = _report(self.sources)
        self.payload_by_artifact = {
            source.artifact.artifact_id: payload
            for source, payload in zip(
                self.sources, (incumbent_payload, alternative_payload), strict=True
            )
            if source.artifact is not None
        }
        self.payload_by_preview = {
            source.artifact.preview_digest: payload
            for source, payload in zip(
                self.sources, (incumbent_payload, alternative_payload), strict=True
            )
            if source.artifact is not None
        }
        self.store = _Store()
        self.now = 10
        self.service = CoverConsiderationService(
            self.store,
            read_preview=lambda artifact, _variant: self.payload_by_artifact[artifact],
            read_preview_digest=lambda digest: self.payload_by_preview[digest],
            clock=lambda: self.now,
        )
        self.proposal = {"proposal_id": "review_proposal_" + "a" * 64}


@pytest.fixture
def case() -> _Case:
    return _Case()


def test_model_free_manual_path_is_persisted_before_exact_selection(
    case: _Case,
) -> None:
    value = case.service.ensure(
        "review_case_" + "b" * 64,
        case.report.to_json(),
        case.proposal,
    )
    assert value["reviewer_status"] == "unavailable"
    assert value["candidates"][0]["incumbent"]
    alternative = value["candidates"][1]
    assert alternative["selectable"]
    # Only a work-route audiobook cover carries the matched-book note (#1642).
    assert [candidate["match_reason"] for candidate in value["candidates"]] == [
        None,
        None,
    ]
    case.service.validate_decision(
        "review_case_" + "b" * 64,
        value["consideration_id"],
        "accept",
        {"kind": "cover", "value": alternative["artifact_id"]},
    )
    with pytest.raises(CoverConsiderationError, match="invalid_choice"):
        case.service.validate_decision(
            "review_case_" + "b" * 64,
            value["consideration_id"],
            "accept",
            {"kind": "cover", "value": value["candidates"][0]["artifact_id"]},
        )


@pytest.mark.parametrize(
    "outcome,reasons,asks",
    (
        ("keep", ["current_cover_verified"], False),
        (
            "unresolved_choice",
            ["current_cover_verified", "owner_cover_choice_required"],
            False,
        ),
        (
            "unresolved_choice",
            [
                "current_cover_unavailable",
                "current_cover_unverified",
                "owner_cover_choice_required",
            ],
            True,
        ),
    ),
)
def test_a_verified_current_cover_is_kept_without_asking(
    outcome: str, reasons: list[str], asks: bool
) -> None:
    """A readable current cover stays choosable but never asks (#1310)."""
    assert (
        _needs_attention(
            {
                "destination": "cover",
                "manual_required": False,
                "result": {"outcome": outcome, "reasons": reasons},
            }
        )
        is asks
    )


def test_identical_replay_reopens_and_explicit_retry_creates_successor(
    case: _Case,
) -> None:
    case_id = "review_case_" + "c" * 64
    first = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    replay = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    assert replay["consideration_id"] == first["consideration_id"]
    case.now += 1
    successor = case.service.ensure(
        case_id, case.report.to_json(), case.proposal, retry=True
    )
    assert successor["supersedes_id"] == first["consideration_id"]
    assert [event["kind"] for event in case.store.events] == [
        "automatic_prepared",
        "superseded",
        "owner_retry_prepared",
    ]


def test_retry_projection_failure_after_commit_is_not_a_refusal(
    case: _Case,
) -> None:
    # The browser reads this bare failure as uncertain because the retry's
    # record has already landed (#1572).
    case_id = "review_case_" + "r" * 64
    first = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    case.now += 1
    with (
        patch.object(
            CoverConsiderationService,
            "_project",
            side_effect=ValueError("synthetic projection failure"),
        ),
        pytest.raises(ValueError, match="synthetic projection failure") as raised,
    ):
        case.service.ensure(case_id, case.report.to_json(), case.proposal, retry=True)
    assert not isinstance(raised.value, CoverConsiderationError)
    assert [event["kind"] for event in case.store.events] == [
        "automatic_prepared",
        "superseded",
        "owner_retry_prepared",
    ]
    successor = case.service.current(case_id)
    assert successor is not None
    assert successor["consideration_id"] != first["consideration_id"]


def test_active_fetching_blocks_readiness_and_quiescence_creates_successor(
    case: _Case,
) -> None:
    fetching = True
    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        provider_fetching=lambda _report: fetching,
    )
    case_id = "review_case_" + "q" * 64

    active = service.ensure(case_id, case.report.to_json(), case.proposal)

    assert active["provider_fetching"] is True
    assert active["ready"] is False
    assert active["policy"]["status"] == "abstained"
    assert active["policy"]["recommended_candidate_reference"] is None
    assert [candidate["deterministic_rank"] for candidate in active["candidates"]] == [
        None,
        1,
    ]
    incumbent = next(
        candidate for candidate in active["candidates"] if candidate["incumbent"]
    )
    assert incumbent["match_state"] == "match_not_confirmed"
    assert incumbent["selectable"] is False
    assert incumbent["state"] == "excluded"

    fetching = False
    assert service.current(case_id) is None
    quiescent = service.ensure(case_id, case.report.to_json(), case.proposal)

    assert quiescent["supersedes_id"] == active["consideration_id"]
    assert quiescent["provider_fetching"] is False
    assert quiescent["ready"] is True
    assert quiescent["policy"]["status"] == "abstained"
    assert quiescent["policy"]["actual_target_state"] == "unknown"
    assert quiescent["policy"]["recommended_candidate_reference"] is None
    assert any(candidate["selectable"] for candidate in quiescent["candidates"])


def test_incumbent_duplicate_remains_bound_to_abs_original(case: _Case) -> None:
    payload = _preview(7)
    incumbent = _source(1, payload, incumbent=True)
    larger_duplicate = _source(9, payload, embedded=True)
    report = _report((incumbent, larger_duplicate))
    assert incumbent.artifact is not None
    assert larger_duplicate.artifact is not None
    store = _Store()
    service = CoverConsiderationService(
        store,
        read_preview=lambda _artifact, _variant: payload,
        read_preview_digest=lambda _digest: payload,
        clock=lambda: 10,
    )

    value = service.ensure(
        "review_case_" + "d" * 64,
        report.to_json(),
        case.proposal,
    )

    assert len(value["candidates"]) == 1
    assert value["candidates"][0]["incumbent"]
    assert value["candidates"][0]["artifact_id"] == incumbent.artifact.artifact_id
    assert (
        json.loads(store.current["review_case_" + "d" * 64])["incumbent_digest"]
        == incumbent.artifact.original_digest
    )


def test_exact_policy_preferences_are_persisted_and_projected(case: _Case) -> None:
    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        policy=CoverPolicy(True, True, True),
    )
    case_id = "review_case_" + "e" * 64

    value = service.ensure(case_id, case.report.to_json(), case.proposal)

    assert value["policy_preferences"] == {
        "avoid_promotional_text": True,
        "avoid_tie_in_branding": True,
        "current_protection": "protected",
        "prefer_edition": False,
        "prefer_minimal_composition": True,
        "schema_version": COVER_POLICY_SCHEMA,
    }
    persisted = json.loads(case.store.current[case_id])
    assert json.loads(persisted["policy_document"]) == value["policy_preferences"]
    assert persisted["policy_reference"] == value["policy_reference"]


def test_live_policy_update_changes_the_next_evaluation_and_rolls_back_on_binding_fault(
    case: _Case,
) -> None:
    bindings: list[tuple[str, str]] = []
    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        bind_policy=lambda policy, replay, _level: bindings.append((policy, replay)),
    )
    changed = CoverPolicy(True, True, True)

    with service.policy_update(changed, WorkIdentityLevel.IDENTIFIER):
        pass
    value = service.ensure(
        "review_case_" + "9" * 64, case.report.to_json(), case.proposal
    )

    assert value["policy_preferences"] == json.loads(changed.document)
    assert bindings == [(changed.reference, service.replay_reference)]

    previous_replay = service.replay_reference
    service._bind_policy = lambda _policy, _replay, _level: (_ for _ in ()).throw(
        RuntimeError("synthetic binding fault")
    )
    with pytest.raises(RuntimeError, match="binding fault"):
        service.update_policy(CoverPolicy())
    assert service.replay_reference == previous_replay


def test_changed_reviewer_contract_makes_the_prior_consideration_noncurrent(
    case: _Case,
) -> None:
    case_id = "review_case_" + "f" * 64
    first = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    changed = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now + 1,
        reviewer_profile=b"changed-profile",
    )

    assert changed.project(case.store.current[case_id]) is None
    with pytest.raises(CoverConsiderationError, match="stale"):
        changed.validate_decision(
            case_id,
            str(first["consideration_id"]),
            "keep_current",
            None,
        )
    successor = changed.ensure(case_id, case.report.to_json(), case.proposal)
    assert first["consideration_id"] == successor["supersedes_id"]


def test_prior_packet_producer_consideration_is_historical_and_superseded_once(
    case: _Case,
) -> None:
    case_id = "review_case_" + "p" * 64
    case.service.ensure(case_id, case.report.to_json(), case.proposal)
    prior_replay = (
        "cover_replay_b296772d7cdce148b5baaff5c3a95217ee58b52fd3940e1231a40d519b263c99"
    )
    persisted = json.loads(case.store.current[case_id])
    persisted["replay_reference"] = prior_replay
    persisted["replay_digest"] = cover_consideration_replay_digest(
        case_id,
        persisted["report_id"],
        provider_fetching=persisted["provider_fetching"],
        replay_reference=prior_replay,
    )
    identity = dict(persisted)
    identity.pop("consideration_id")
    identity.pop("created_at")
    persisted["consideration_id"] = (
        "cover_consideration_"
        + hashlib.sha256(canonical_packet_json(identity).encode()).hexdigest()
    )
    case.store.current[case_id] = canonical_packet_json(persisted)
    prior_consideration_id = persisted["consideration_id"]

    comparison, currentness = case.service.project_for_review(
        case.store.current[case_id], case_id, case.report.to_json()
    )
    assert currentness == "refresh_required"
    assert comparison is not None
    assert comparison["current"] is False
    assert (
        case.service.project_historical(case.store.current[case_id])["current"] is False
    )
    assert case.service.current(case_id) is None
    successor = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    replay = case.service.ensure(case_id, case.report.to_json(), case.proposal)

    assert successor["supersedes_id"] == prior_consideration_id
    assert replay["consideration_id"] == successor["consideration_id"]
    assert [event["kind"] for event in case.store.events] == [
        "automatic_prepared",
        "superseded",
        "automatic_prepared",
    ]


@pytest.mark.parametrize(
    "document,replay_reference",
    (("[]", "cover_replay_" + "1" * 64), ("{}", "invalid"), ("not-json", "x")),
)
def test_shared_consideration_currentness_fails_closed(
    document: str, replay_reference: str
) -> None:
    assert (
        cover_consideration_is_current(
            document, replay_reference, lambda _report: False
        )
        is False
    )


def test_shared_consideration_currentness_fails_closed_when_recovery_is_unavailable(
    case: _Case,
) -> None:
    case_id = "review_case_" + "q" * 64
    case.service.ensure(case_id, case.report.to_json(), case.proposal)

    def unavailable(_report: MetadataReport) -> bool:
        raise RuntimeError("synthetic recovery store failure")

    assert (
        cover_consideration_is_current(
            case.store.current[case_id], case.service.replay_reference, unavailable
        )
        is False
    )


def test_currentness_facts_derive_from_complete_producer(case: _Case) -> None:
    case_id = "review_case_" + "r" * 64
    case.service.ensure(case_id, case.report.to_json(), case.proposal)
    document = case.store.current[case_id]
    assert json.loads(cover_consideration_currentness_document(document)) == {
        "connection": case.report.target.connection,
        "library": case.report.target.library,
        "item": case.report.target.item,
        "report_id": case.report.report_id,
        "provider_fetching": False,
        "manual_ready": True,
        "replay_reference": case.service.replay_reference,
        "cover_stage": {
            "outcome": json.loads(json.loads(document)["policy_report_document"])[
                "stage"
            ]["outcome"],
            "reason": json.loads(json.loads(document)["policy_report_document"])[
                "stage"
            ]["reason"],
        },
    }
    facts = cover_consideration_currentness_document(document)
    observed: list[tuple[str, str, str, str]] = []

    def fetching(connection: str, library: str, item: str, report: str) -> bool:
        observed.append((connection, library, item, report))
        return False

    assert (
        cover_currentness_from_facts(facts, case.service.replay_reference, fetching)
        == "current"
    )
    assert observed == [
        (
            case.report.target.connection,
            case.report.target.library,
            case.report.target.item,
            case.report.report_id,
        )
    ]
    assert (
        cover_currentness_from_facts(facts, "cover_replay_" + "0" * 64, fetching)
        == "refresh_required"
    )
    assert (
        cover_currentness_from_facts(
            facts, case.service.replay_reference, lambda *_: True
        )
        == "inactive"
    )
    for invalid in ("{}", "[]", "null", "not-json"):
        assert cover_consideration_currentness_document(invalid) == "null"


def test_shared_consideration_currentness_distinguishes_current_and_refresh(
    case: _Case,
) -> None:
    case_id = "review_case_" + "r" * 64
    case.service.ensure(case_id, case.report.to_json(), case.proposal)
    document = case.store.current[case_id]

    assert (
        cover_consideration_currentness(
            document, case.service.replay_reference, lambda _report: False
        )
        == "current"
    )
    assert (
        cover_consideration_currentness(
            document, "cover_replay_" + "0" * 64, lambda _report: False
        )
        == "refresh_required"
    )


@pytest.mark.parametrize("live_fetching", (True, 0, None))
def test_shared_consideration_currentness_rejects_fetching_mismatch_and_non_bool(
    case: _Case, live_fetching: object
) -> None:
    case_id = "review_case_" + "s" * 64
    case.service.ensure(case_id, case.report.to_json(), case.proposal)

    assert (
        cover_consideration_currentness(
            case.store.current[case_id],
            "cover_replay_" + "0" * 64,
            lambda _report: cast("bool", live_fetching),
        )
        == "inactive"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("unexpected", True),
        ("replay_digest", "0" * 64),
        ("policy_document", "{}"),
        ("provider_fetching", 0),
    ),
)
def test_shared_consideration_currentness_rejects_malformed_authenticated_shapes(
    case: _Case, field: str, value: object
) -> None:
    case_id = "review_case_" + "t" * 64
    case.service.ensure(case_id, case.report.to_json(), case.proposal)
    persisted = json.loads(case.store.current[case_id])
    persisted[field] = value
    identity = dict(persisted)
    identity.pop("consideration_id")
    identity.pop("created_at")
    persisted["consideration_id"] = (
        "cover_consideration_"
        + hashlib.sha256(canonical_packet_json(identity).encode()).hexdigest()
    )
    assert (
        cover_consideration_currentness_document(canonical_packet_json(persisted))
        == "null"
    )
    calls: list[MetadataReport] = []

    def provider_fetching(report: MetadataReport) -> bool:
        calls.append(report)
        return False

    assert (
        cover_consideration_currentness(
            canonical_packet_json(persisted),
            "cover_replay_" + "0" * 64,
            provider_fetching,
        )
        == "inactive"
    )
    assert calls == []


@pytest.mark.parametrize("saved_replay", ("current", "prior"))
@pytest.mark.parametrize(
    "malformation",
    (
        "partial_candidate",
        "partial_policy_report",
        "identity_candidate_reference",
        "identity_report_digest",
    ),
)
def test_nested_consideration_evidence_fails_closed_before_provider_recovery(
    case: _Case,
    saved_replay: str,
    malformation: str,
) -> None:
    case_id = "review_case_" + "u" * 64
    case.service.ensure(case_id, case.report.to_json(), case.proposal)
    persisted = json.loads(case.store.current[case_id])
    if malformation == "partial_candidate":
        persisted["candidates"] = [{}]
    elif malformation == "partial_policy_report":
        policy = json.loads(persisted["policy_report_document"])
        persisted["policy_report_document"] = canonical_packet_json(
            {
                name: policy[name]
                for name in ("owner_ready", "policy_reference", "schema_version")
            }
        )
    elif malformation == "identity_candidate_reference":
        identity_document = json.loads(persisted["identity_document"])
        identity_document["candidates"][0]["candidate_reference"] = (
            "cover_ref_" + "0" * 64
        )
        persisted["identity_document"] = canonical_packet_json(identity_document)
        policy = json.loads(persisted["policy_report_document"])
        policy["identity_report_sha256"] = hashlib.sha256(
            persisted["identity_document"].encode()
        ).hexdigest()
        persisted["policy_report_document"] = canonical_packet_json(policy)
    else:
        policy = json.loads(persisted["policy_report_document"])
        policy["identity_report_sha256"] = "0" * 64
        persisted["policy_report_document"] = canonical_packet_json(policy)
    if saved_replay == "prior":
        prior_replay = "cover_replay_" + "0" * 64
        persisted["replay_reference"] = prior_replay
        persisted["replay_digest"] = cover_consideration_replay_digest(
            case_id,
            persisted["report_id"],
            provider_fetching=persisted["provider_fetching"],
            replay_reference=prior_replay,
        )
    identity = dict(persisted)
    identity.pop("consideration_id")
    identity.pop("created_at")
    persisted["consideration_id"] = (
        "cover_consideration_"
        + hashlib.sha256(canonical_packet_json(identity).encode()).hexdigest()
    )
    calls: list[MetadataReport] = []

    def provider_fetching(report: MetadataReport) -> bool:
        calls.append(report)
        return False

    assert (
        cover_consideration_currentness(
            canonical_packet_json(persisted),
            case.service.replay_reference,
            provider_fetching,
        )
        == "inactive"
    )
    assert calls == []


def test_malformed_nested_consideration_has_no_projection_or_choice_authority(
    case: _Case,
) -> None:
    case_id = "review_case_" + "v" * 64
    current = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    persisted = json.loads(case.store.current[case_id])
    reference = persisted["candidates"][0]["reference"]
    persisted["candidates"] = [{}]
    identity = dict(persisted)
    identity.pop("consideration_id")
    identity.pop("created_at")
    persisted["consideration_id"] = (
        "cover_consideration_"
        + hashlib.sha256(canonical_packet_json(identity).encode()).hexdigest()
    )
    document = canonical_packet_json(persisted)
    case.store.current[case_id] = document

    assert case.service.current(case_id) is None
    assert case.service.project_for_review(
        document, case_id, case.report.to_json()
    ) == (None, "inactive")
    with pytest.raises(CoverConsiderationError, match="^persistence$"):
        _ = CoverConsideration(document).data
    with pytest.raises(CoverConsiderationError, match="^stale$"):
        case.service.validate_decision(
            case_id,
            persisted["consideration_id"],
            "keep_current",
            None,
        )
    with pytest.raises(CoverConsiderationError, match="^stale$"):
        case.service.image(case_id, persisted["consideration_id"], reference)
    assert current["consideration_id"] != persisted["consideration_id"]


def test_absent_mismatch_binding_preserves_existing_replays_and_decisions(
    case: _Case,
) -> None:
    case_id = "review_case_" + "i" * 64
    first = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    alternative = first["candidates"][1]
    case.service.validate_decision(
        case_id,
        first["consideration_id"],
        "accept",
        {"kind": "cover", "value": alternative["artifact_id"]},
    )

    model_free = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now + 1,
    )
    assert model_free.replay_reference == (
        "cover_replay_9794008d7f49e247184cdc2129400dc6a1e2b3fa03adb73af1f6dde874f78374"
    )
    assert (
        model_free.ensure(case_id, case.report.to_json(), case.proposal)[
            "consideration_id"
        ]
        == first["consideration_id"]
    )
    model_free.validate_decision(
        case_id,
        first["consideration_id"],
        "accept",
        {"kind": "cover", "value": alternative["artifact_id"]},
    )

    dispatches: list[object] = []

    def reviewer(*args: object) -> str:
        dispatches.append(args)
        return _observer_result(
            *cast(
                "tuple[bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]]",
                args,
            )
        )

    def qualified() -> CoverConsiderationService:
        return CoverConsiderationService(
            case.store,
            read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
            read_preview_digest=lambda digest: case.payload_by_preview[digest],
            clock=lambda: case.now,
            reviewer_profile=b"qualified-profile",
            reviewer=cast("ReviewRunner", reviewer),
        )

    qualified_case_id = "review_case_" + "j" * 64
    first_qualified = qualified().ensure(
        qualified_case_id, case.report.to_json(), case.proposal
    )
    assert len(dispatches) == 1
    replayed_qualified = qualified()
    assert replayed_qualified.replay_reference == (
        "cover_replay_ea65f712b1f8ce7fc7b912f3f49d59defb420811f95cf40acaa2a3185bc46748"
    )
    assert (
        replayed_qualified.ensure(
            qualified_case_id, case.report.to_json(), case.proposal
        )["consideration_id"]
        == first_qualified["consideration_id"]
    )
    assert len(dispatches) == 1
    replayed = replayed_qualified.ensure(
        qualified_case_id, case.report.to_json(), case.proposal
    )
    assert replayed["candidates"] == first_qualified["candidates"]
    assert (
        model_free.ensure(case_id, case.report.to_json(), case.proposal)["candidates"]
        == first["candidates"]
    )


def test_legacy_observations_never_project_as_complete_current_visual_checks(
    case: _Case,
) -> None:
    def reviewer(*args: object) -> str:
        return _observer_result(
            *cast(
                "tuple[bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]]",
                args,
            )
        )

    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        reviewer_profile=b"qualified-profile",
        reviewer=cast("ReviewRunner", reviewer),
    )
    case_id = "review_case_" + "k" * 64
    service.ensure(case_id, case.report.to_json(), case.proposal)
    persisted = json.loads(case.store.current[case_id])
    observation = json.loads(cast("str", persisted["observation_document"]))
    observation["schema_version"] = cover_observation.PREVIOUS_SCHEMA_VERSION

    candidates = service._candidates(
        case.report,
        cast("str", persisted["packet_document"]),
        cast("str", persisted["identity_document"]),
        json.dumps(observation),
        cast("str", persisted["policy_report_document"]),
    )

    assert candidates
    assert all(candidate["observation"] is None for candidate in candidates)


def test_stale_reviewer_binding_is_current_only_and_replays_exactly(
    case: _Case,
) -> None:
    case_id = "review_case_" + "g" * 64

    def failed_reviewer(*_args: object) -> str:
        raise RuntimeError("contained reviewer exited")

    def service(
        profile: bytes | None, reason: str | None = None
    ) -> CoverConsiderationService:
        return CoverConsiderationService(
            case.store,
            read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
            read_preview_digest=lambda digest: case.payload_by_preview[digest],
            clock=lambda: case.now,
            reviewer_profile=profile,
            reviewer=(
                cast("ReviewRunner", failed_reviewer)
                if reason is None and profile
                else None
            ),
            reviewer_unavailable_reason=reason,
        )

    generic = service(b"stale-profile")
    first = generic.ensure(case_id, case.report.to_json(), case.proposal)
    stale = service(
        b"stale-profile", "profile contract requires memory_bytes=1073741824"
    )
    successor = stale.ensure(case_id, case.report.to_json(), case.proposal)
    persisted = json.loads(case.store.current[case_id])

    assert generic.replay_reference != stale.replay_reference
    assert successor["supersedes_id"] == first["consideration_id"]
    assert successor["reviewer_unavailable_reason"] == (
        "profile contract requires memory_bytes=1073741824"
    )
    assert persisted["reviewer_failure"] is None
    assert "reviewer_unavailable_reason" not in persisted
    assert (
        stale.project_historical(case.store.current[case_id])[
            "reviewer_unavailable_reason"
        ]
        is None
    )
    assert (
        stale.ensure(case_id, case.report.to_json(), case.proposal)["consideration_id"]
        == successor["consideration_id"]
    )
    different_service = service(
        b"other-stale-profile", "profile contract requires process_limit=32"
    )
    different = different_service.ensure(case_id, case.report.to_json(), case.proposal)
    assert different["supersedes_id"] == successor["consideration_id"]
    assert different["reviewer_unavailable_reason"] == (
        "profile contract requires process_limit=32"
    )
    different_document = json.loads(case.store.current[case_id])
    assert different_document["schema_version"] == COVER_CONSIDERATION_SCHEMA
    assert different_document["reviewer_failure"] is None
    assert "reviewer_unavailable_reason" not in different_document

    model_free_case = "review_case_" + "h" * 64
    model_free = service(None)
    model_free_value = model_free.ensure(
        model_free_case, case.report.to_json(), case.proposal
    )
    stale_model_free = stale.ensure(
        model_free_case, case.report.to_json(), case.proposal
    )
    assert model_free.replay_reference != stale.replay_reference
    assert stale_model_free["supersedes_id"] == model_free_value["consideration_id"]


def test_preview_store_failure_is_typed_unavailable(case: _Case) -> None:
    case_id = "review_case_" + "1" * 64
    value = case.service.ensure(case_id, case.report.to_json(), case.proposal)

    def unavailable(_digest: str) -> bytes:
        raise RuntimeError("synthetic store failure")

    broken = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=unavailable,
        clock=lambda: case.now,
    )
    candidate = value["candidates"][0]
    with pytest.raises(CoverConsiderationError, match="unavailable"):
        broken.image(
            case_id,
            str(value["consideration_id"]),
            str(candidate["reference"]),
        )


def test_local_projection_fault_is_not_recorded_as_reviewer_failure(
    case: _Case,
) -> None:
    """Only the contained process can produce reviewer failure evidence.

    The handler wrapped all of `observe_cover_packet` -- the packet binding,
    the schema construction, the envelope parse, and the result validation
    -- not just the reviewer dispatch. A fault in this layer's own
    projection was therefore recorded as `reviewer_status="failed"` with
    reviewer failure evidence, and the workflow continued as though the
    contained process had failed. Everything before the dispatch is ours,
    so a failure there surfaces instead (#698).
    """
    dispatches: list[object] = []

    def reviewer(*args: object) -> str:
        dispatches.append(args)
        raise RuntimeError("contained reviewer exited")

    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        reviewer_profile=b"profile",
        reviewer=cast("ReviewRunner", reviewer),
    )

    # A failure of the contained process is still one reviewable outcome.
    value = service.ensure(
        "review_case_" + "2" * 64, case.report.to_json(), case.proposal
    )
    assert value["reviewer_status"] == "failed"
    assert len(dispatches) == 1

    # A fault in the projection that runs before the reviewer is reached is
    # not a reviewer outcome, and never reaches the reviewer at all.
    fault = RuntimeError("cover request projection is broken")
    with patch.object(
        cover_observation, "admit_cover_review_request", side_effect=fault
    ):
        with pytest.raises(RuntimeError) as surfaced:
            service.ensure(
                "review_case_" + "3" * 64,
                case.report.to_json(),
                case.proposal,
            )
    assert surfaced.value is fault
    assert len(dispatches) == 1


def test_assembly_fault_after_the_reviewer_returns_is_not_reviewer_failure(
    case: _Case,
) -> None:
    """The reviewer's return is now a boundary the handler can see.

    The dispatch flag marked the reviewer's call but not its return, so the
    report `observe_cover_packet` assembles afterwards -- row binding, the
    two digests, `_observer_reference` and the canonicalisation -- still
    counted as dispatched. A fault in that assembly was persisted as
    `reviewer_status="failed"` with reviewer failure evidence, blaming the
    contained process for a projection it never touched. `observe_cover_packet`
    now signals the validated return, so the far end of the reviewer's
    contribution is as visible to the handler as the near end (#698).
    """
    dispatches: list[object] = []

    def reviewer(*args: object) -> str:
        dispatches.append(args)
        return _observer_result(
            *cast(
                "tuple[bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]]",
                args,
            )
        )

    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        reviewer_profile=b"profile",
        reviewer=cast("ReviewRunner", reviewer),
    )

    # A reviewer that returns a valid result is still one completed
    # observation, so the boundary does not cost the ordinary path.
    value = service.ensure(
        "review_case_" + "4" * 64, case.report.to_json(), case.proposal
    )
    assert value["reviewer_status"] == "completed"
    assert len(dispatches) == 1

    # A fault in the assembly that runs after the validated return is ours,
    # and the reviewer is not asked again to produce it.
    fault = RuntimeError("observer reference is broken")
    with patch.object(cover_observation, "_observer_reference", side_effect=fault):
        with pytest.raises(RuntimeError) as surfaced:
            service.ensure(
                "review_case_" + "5" * 64,
                case.report.to_json(),
                case.proposal,
            )
    assert surfaced.value is fault
    assert len(dispatches) == 2
    assert "review_case_" + "5" * 64 not in case.store.current


def test_each_served_image_is_the_artifact_that_choice_would_apply(
    case: _Case,
) -> None:
    """The bytes under a reference and the artifact a decision records are one row.

    #644 accepts that "the image shown for selection is the exact admitted
    artifact that the guarded cover path would apply". The surface addresses an
    image by `reference`; `validate_decision` addresses a choice by
    `artifact_id`. Both resolve against the same candidate row of one current
    consideration document, so this asserts the pairing directly rather than
    trusting that two lookups happen to agree.
    """
    case_id = "review_case_" + "d" * 64
    value = case.service.ensure(case_id, case.report.to_json(), case.proposal)
    consideration_id = str(value["consideration_id"])
    served: dict[str, bytes] = {}
    for candidate in value["candidates"]:
        payload, retained = case.service.image(
            case_id, consideration_id, str(candidate["reference"])
        )
        assert retained is False
        assert hashlib.sha256(payload).hexdigest() == candidate["preview_sha256"]
        assert payload == case.payload_by_artifact[candidate["artifact_id"]]
        served[str(candidate["reference"])] = payload
    # Distinct references never serve one another's bytes.
    assert len(set(served.values())) == len(served)
    selectable = next(
        candidate
        for candidate in value["candidates"]
        if candidate["selectable"] and not candidate["incumbent"]
    )
    case.service.validate_decision(
        case_id,
        consideration_id,
        "accept",
        {"kind": "cover", "value": selectable["artifact_id"]},
    )
    with pytest.raises(CoverConsiderationError, match="not_found"):
        case.service.image(case_id, consideration_id, "cover_ref_" + "f" * 64)
    with pytest.raises(CoverConsiderationError, match="invalid_choice"):
        case.service.validate_decision(
            case_id,
            consideration_id,
            "accept",
            {"kind": "cover", "value": "cover_artifact_" + "f" * 64},
        )


def _audiobook_case() -> tuple[
    CoverConsiderationService, str, list[str], dict[str, object]
]:
    """One audiobook report whose two provider covers were really retrieved."""
    payloads = (_preview(41), _preview(42))
    sources = (
        _audiobook_cover_source(
            41,
            payloads[0],
            CoverSourceKind.AUDIBLE_CATALOG,
            "audible",
            "0f1e2d3c4b5a6978",
        ),
        _audiobook_cover_source(
            42,
            payloads[1],
            CoverSourceKind.ITUNES_SEARCH,
            "store",
            "0f1e2d3c4b5a6979",
        ),
    )
    report = _audiobook_report(sources)
    by_artifact = {
        source.artifact.artifact_id: payload
        for source, payload in zip(sources, payloads, strict=True)
        if source.artifact is not None
    }
    by_preview = {
        source.artifact.preview_digest: payload
        for source, payload in zip(sources, payloads, strict=True)
        if source.artifact is not None
    }
    dispatches: list[str] = []

    def reviewer(*args: object) -> str:
        dispatches.append("dispatched")
        return _observer_result(
            *cast(
                "tuple[bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]]",
                args,
            )
        )

    service = CoverConsiderationService(
        _Store(),
        read_preview=lambda artifact, _variant: by_artifact[artifact],
        read_preview_digest=lambda digest: by_preview[digest],
        clock=lambda: 10,
        reviewer_dispatch=lambda: ReviewerDispatch(
            7,
            "openrouter",
            "example/vision",
            b"profile",
            cast("ReviewRunner", reviewer),
        ),
        reviewer_identity=lambda: (7, "openrouter", "example/vision", b"profile"),
        reviewer_revision_current=lambda revision: revision == 7,
    )
    return (
        service,
        report.to_json(),
        dispatches,
        {"proposal_id": "review_proposal_" + "c" * 64},
    )


def test_an_audiobook_with_retrieved_covers_builds_no_cover_packet() -> None:
    """Acquisition is evidence; consideration is a decision nobody may make."""
    service, document, dispatches, _proposal = _audiobook_case()

    request = service.prepare_observation("review_case_" + "d" * 64, document)

    assert request.packet_payload is None
    assert () == request.image_payloads
    assert [] == dispatches


@pytest.mark.parametrize("recording", [True, False], ids=["recording", "work"])
def test_an_audiobook_consideration_dispatches_no_observer(
    case: _Case,
    recording: bool,
) -> None:
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_audiobook_identity,
    )
    from mechanical_librarian.domain.proposal_set import audiobook_review_scope
    from tests.support.audiobook_synopsis import synopsis_report

    service, document, dispatches, proposal = _audiobook_case()

    original_report = MetadataReport.from_json(document)
    report = original_report
    if not recording:
        work = synopsis_report()
        report = MetadataReport.create(
            target=work.target,
            file=work.file,
            local_claims=work.local_claims,
            local_reasons=work.local_reasons,
            providers=work.providers,
            audio=work.audio,
            cover_sources=original_report.cover_sources,
        )
        document = report.to_json()
    assert admitted_audiobook_identity(report) is recording
    assert audiobook_review_scope(report) == ("recording" if recording else "work")
    projected = service.ensure("review_case_" + "e" * 64, document, proposal)

    assert [] == dispatches
    assert "unavailable" == projected["reviewer_status"]
    if recording:
        assert len(projected["candidates"]) == 2
        for candidate in projected["candidates"]:
            assert candidate["state"] == "excluded"
            assert candidate["manual_eligible"] is True
            assert candidate["selectable"] is False
            assert candidate["identity"]["outcome"] == "unknown"
            assert "unbound_provider_identity" in candidate["identity"]["reasons"]
            assert candidate["observation"] is None
            # A recording-route cover carries no matched-book note (#1642).
            assert candidate["match_reason"] is None
            service.validate_decision(
                "review_case_" + "e" * 64,
                projected["consideration_id"],
                "accept",
                {"kind": "cover", "value": candidate["artifact_id"]},
            )
        assert projected["policy"]["status"] == "abstained"
        assert projected["policy"]["recommended_candidate_reference"] is None
    else:
        assert projected["candidates"] == []
        assert projected["ready"] is False
        artifact = original_report.cover_sources[0].artifact
        assert artifact is not None
        with pytest.raises(CoverConsiderationError, match="invalid_choice"):
            service.validate_decision(
                "review_case_" + "e" * 64,
                projected["consideration_id"],
                "accept",
                {
                    "kind": "cover",
                    "value": artifact.artifact_id,
                },
            )
    persisted = json.loads(
        cast("_Store", service._store).current["review_case_" + "e" * 64]
    )
    assert (persisted["packet_document"] is not None) is recording
    assert persisted["observation_document"] is None
    assert (persisted["identity_document"] is not None) is recording
    assert (persisted["policy_report_document"] is not None) is recording
    # The same evidence on the ebook path still packets and still observes.
    assert case.service.ensure(
        "review_case_" + "f" * 64, case.report.to_json(), case.proposal
    )["candidates"]


@pytest.mark.parametrize(
    "duration,supported", [("58200.000", True), ("100.000", False)]
)
def test_recording_bound_cover_reaches_deterministic_consumer_without_observer(
    duration: str, supported: bool
) -> None:
    service, original_document, dispatches, proposal = _audiobook_case()
    original = MetadataReport.from_json(original_document)
    base = asserted_asin_report(provider_duration=duration)
    provider = base.providers[0]
    candidate = provider.candidates[0]
    source = original.cover_sources[0]
    assert source.provenance is not None
    source = replace(
        source,
        provenance=replace(
            source.provenance,
            provider=provider.provider,
            work_identity=candidate.work_identity,
            edition_identity=candidate.identity,
        ),
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        audio=base.audio,
        cover_sources=(source,),
    )
    request = service.prepare_observation("review_case_" + "e" * 64, report.to_json())
    assert request.packet_payload is None
    actual = service.ensure("review_case_" + "e" * 64, report.to_json(), proposal)
    assert dispatches == []
    assert actual["reviewer_status"] == "unavailable"
    assert len(actual["candidates"]) == int(supported)
    if supported:
        elected = actual["candidates"][0]
        assert elected["identity"]["outcome"] == "verified_work_match"
        assert elected["identity"]["reasons"] == ["retained_recording_admission"]
        assert elected["selectable"] is True
        assert elected["deterministic_rank"] == 1


@pytest.mark.parametrize(
    "provider_creators, eligible",
    [(("Ada Author",), True), (("Different Author",), False)],
)
def test_manual_choice_preserves_typed_identity_and_refuses_creator_disagreement(
    provider_creators, eligible
):
    from tests.unit.test_cover_packets import _typed_discovery_identity_case

    report, _, _, payload = _typed_discovery_identity_case(
        local_creators=("Ada Author",), provider_creators=provider_creators
    )
    store = _Store()
    service = CoverConsiderationService(
        store,
        read_preview=lambda *_: payload,
        read_preview_digest=lambda _: payload,
        clock=lambda: 10,
    )
    case_id = "review_case_" + "c" * 64
    projection = service.ensure(
        case_id, report, {"proposal_id": "review_proposal_" + "d" * 64}
    )
    before = store.current[case_id]
    candidate = projection["candidates"][0]
    assert candidate["manual_eligible"] is eligible
    assert candidate["selectable"] is False
    assert projection["ready"] is eligible
    if eligible:
        assert candidate["identity"]["outcome"] == "plausible_match"
        service.validate_decision(
            case_id,
            projection["consideration_id"],
            "accept",
            {"kind": "cover", "value": candidate["artifact_id"]},
        )
    else:
        assert candidate["identity"]["outcome"] == "unknown"
        assert (
            "retained_creator_or_contributor_disagreement"
            in candidate["identity"]["reasons"]
        )
        with pytest.raises(CoverConsiderationError, match="invalid_choice"):
            service.validate_decision(
                case_id,
                projection["consideration_id"],
                "accept",
                {"kind": "cover", "value": candidate["artifact_id"]},
            )
    assert store.current[case_id] == before


def test_owner_can_choose_smaller_confirmed_cover_without_changing_policy(tmp_path):
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from tests.support.cover_producers import _qualified_cover_report

    report, _ = _qualified_cover_report(
        tmp_path, incumbent_size=(1200, 1800), candidate_size=(300, 450)
    )
    artifacts = CoverArtifactStore(tmp_path / "artifacts")
    store = _Store()
    service = CoverConsiderationService(
        store,
        read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
            next(
                source.artifact.preview_digest
                for source in report.cover_sources
                if source.artifact is not None
                and source.artifact.artifact_id == artifact_id
            )
        ),
        read_preview_digest=artifacts.read_preview_digest,
        clock=lambda: 10,
    )
    case_id = "review_case_" + "e" * 64
    projection = service.ensure(
        case_id, report.to_json(), {"proposal_id": "review_proposal_" + "f" * 64}
    )
    candidate = next(item for item in projection["candidates"] if not item["incumbent"])
    incumbent = next(item for item in projection["candidates"] if item["incumbent"])
    assert candidate["width"] < incumbent["width"]
    assert candidate["manual_eligible"] is True
    before = store.current[case_id]
    service.validate_decision(
        case_id,
        projection["consideration_id"],
        "accept",
        {"kind": "cover", "value": candidate["artifact_id"]},
    )
    assert store.current[case_id] == before


def test_base_produced_observation_is_refused_and_current_semantics_create_one_successor(
    case: _Case, tmp_path
) -> None:
    from pathlib import Path

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.cover_policy import _observation_rows
    from mechanical_librarian.application.reviewer_contracts import (
        openrouter_cover_profile,
    )

    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/stable-cover-reviewer-predecessor/consideration.json"
        ).read_text()
    )
    assert fixture["source_sha"] == "3a7d15aad11c6dc24aa52eb78a25b5d0cbf9bee8"
    raw = fixture["consideration"]
    # The capture embeds a v49 report, and its packet binds that report's
    # identity, so it cannot reopen as current; its fields are read raw. Current
    # semantics still supersede it with one successor for the ephemeral report.
    with pytest.raises(CoverConsiderationError):
        _ = CoverConsideration(raw).data
    prior = json.loads(raw)
    assert prior["reviewer_status"] == "completed"
    report = retained_report_as_current(fixture["report"])
    packet = json.loads(prior["packet_document"])
    with pytest.raises(ValueError, match="not current"):
        _observation_rows(
            prior["observation_document"],
            packet["packet_reference"],
            packet["candidates"],
            tuple(c["reference"] for c in packet["candidates"]),
            fixture["profile"].encode(),
        )
    store = _Store()
    store.current[prior["review_case_id"]] = raw
    model = "mistralai/ministral-3b-2512"
    profile = openrouter_cover_profile(model)
    dispatches = []

    def reviewer(*args):
        dispatches.append(args)
        return _observer_result(*args)

    service = CoverConsiderationService(
        store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=case.payload_by_preview.__getitem__,
        clock=lambda: 11,
        reviewer_dispatch=lambda: ReviewerDispatch(
            1, "openrouter", model, profile, reviewer
        ),
        reviewer_identity=lambda: (1, "openrouter", model, profile),
        reviewer_revision_current=lambda revision: revision == 1,
    )
    # A cover-consideration-v4 document is refused before its report is read.
    with pytest.raises(ValueError, match="cover consideration document is invalid"):
        service.project_historical(raw)
    current = service.ensure(
        prior["review_case_id"], report.to_json(), fixture["proposal"]
    )
    assert current["reviewer_status"] == "completed"
    assert current["supersedes_id"] == prior["consideration_id"]
    replay = service.ensure(
        prior["review_case_id"], report.to_json(), fixture["proposal"]
    )
    assert replay["consideration_id"] == current["consideration_id"]
    assert len(dispatches) == 1
    assert store.events == [
        {"kind": "superseded", "occurred_at": 11},
        {"kind": "automatic_prepared", "occurred_at": 11},
    ]

    from mechanical_librarian.domain.proposal_set import evaluate_report

    database = tmp_path / "history.sqlite"
    repository = open_repository(database)
    repository.publish(report, lambda: True)
    proposals = evaluate_report(report)
    repository.publish_proposal(proposals)
    admitted = json.loads(repository.admit_review_case(proposals))
    current_case_id = admitted["review_case_id"]
    assert current_case_id != prior["review_case_id"]
    from mechanical_librarian.domain.review_case import ReviewCase

    review_case = ReviewCase.create(proposals, report)
    cover_proposal = next(
        field for field in review_case.proposals if field["destination"] == "cover"
    )
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=case.payload_by_preview.__getitem__,
        clock=lambda: 12,
        reviewer_dispatch=lambda: ReviewerDispatch(
            1, "openrouter", model, profile, reviewer
        ),
        reviewer_identity=lambda: (1, "openrouter", model, profile),
        reviewer_revision_current=lambda revision: revision == 1,
    )
    fresh = service.ensure(current_case_id, report.to_json(), cover_proposal)
    assert fresh["supersedes_id"] is None
    document = repository.current_cover_consideration_document(current_case_id)
    assert len(dispatches) == 2
    repository.close()
    repository = open_repository(database, create=False)
    assert repository.current_cover_consideration_document(current_case_id) == document
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=case.payload_by_preview.__getitem__,
        clock=lambda: 13,
        reviewer_dispatch=lambda: ReviewerDispatch(
            1, "openrouter", model, profile, reviewer
        ),
        reviewer_identity=lambda: (1, "openrouter", model, profile),
        reviewer_revision_current=lambda revision: revision == 1,
    )
    replay = service.ensure(current_case_id, report.to_json(), cover_proposal)
    assert replay["consideration_id"] == fresh["consideration_id"]
    assert len(dispatches) == 2
    assert repository.current_cover_consideration_document(current_case_id) == document
    repository.close()


@pytest.mark.parametrize(
    ("engine", "code", "exposed"),
    (
        ("openrouter", "timed_out", "timed_out"),
        ("openrouter", "data_policy_refused", "data_policy_refused"),
        ("openrouter", "image_limit_exceeded", "image_limit_exceeded"),
        ("openrouter", "response_too_large", "response_too_large"),
        ("openrouter", "transport", None),
        ("codex", "timeout", None),
    ),
)
def test_chat_time_reviewer_failure_exposes_only_a_closed_code(
    case: _Case, engine: str, code: str, exposed: str | None
) -> None:
    from mechanical_librarian.application.reviewer_contracts import (
        OpenRouterReviewerError,
    )
    from mechanical_librarian.interfaces.web.app import _CoverConsiderationResponse

    profile = b"reviewer-profile"

    def reviewer(*_args: object) -> str:
        if engine == "openrouter":
            raise OpenRouterReviewerError(cast("OpenRouterFailure", code))
        error = RuntimeError("private reviewer canary")
        error.code = code
        raise error

    model = "example/vision" if engine == "openrouter" else "gpt-5.6-sol"
    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        reviewer_dispatch=lambda: ReviewerDispatch(
            7,
            cast("ReviewerEngine", engine),
            model,
            profile,
            cast("ReviewRunner", reviewer),
        ),
        reviewer_identity=lambda: (7, engine, model, profile),
        reviewer_revision_current=lambda revision: revision == 7,
    )
    case_id = "review_case_" + "k" * 64
    projected = service.ensure(case_id, case.report.to_json(), case.proposal)
    persisted = json.loads(case.store.current[case_id])

    assert projected["reviewer_status"] == "failed"
    assert projected["reviewer_failure_code"] == exposed
    # The persisted document is unchanged: the open stored code is only read.
    assert persisted["reviewer_failure"]["code"] == code
    assert "reviewer_failure_code" not in persisted
    response = _CoverConsiderationResponse.model_validate(projected)
    assert response.reviewer_failure_code == exposed
    assert "canary" not in response.model_dump_json()

    # A legacy or unknown stored code surfaces as no code, never as an error.
    legacy = dict(persisted, reviewer_failure={"attempts": [], "code": "legacy"})
    identity = {
        key: value
        for key, value in legacy.items()
        if key not in {"consideration_id", "created_at"}
    }
    legacy["consideration_id"] = (
        "cover_consideration_"
        + hashlib.sha256(canonical_packet_json(identity).encode()).hexdigest()
    )
    historical = service.project_historical(canonical_packet_json(legacy))
    assert historical["reviewer_failure_code"] is None


def test_a_work_route_cover_is_an_owner_choice_with_no_observer() -> None:
    """A recording-less audiobook's work cover is offered, never observed (#1642)."""
    from mechanical_librarian.domain.proposal_set import (
        audiobook_review_scope,
        work_route_cover_offered,
    )
    from tests.support.hardcover_work_route import (
        hardcover_work_report,
        work_cover_source,
    )

    service, _document, dispatches, proposal = _audiobook_case()
    payload = _preview(51)
    source = work_cover_source(preview=payload)
    report = hardcover_work_report(cover_sources=(source,))
    assert audiobook_review_scope(report) == "work"
    assert work_route_cover_offered(report)
    artifact = source.artifact
    assert artifact is not None
    with (
        patch.object(service, "_read_preview", lambda _artifact, _variant: payload),
        patch.object(
            service,
            "_read_preview_digest",
            {artifact.preview_digest: payload}.__getitem__,
        ),
    ):
        request = service.prepare_observation(
            "review_case_" + "e" * 64, report.to_json()
        )
        assert request.packet_payload is None
        projected = service.ensure(
            "review_case_" + "e" * 64, report.to_json(), proposal
        )

        assert [] == dispatches
        assert "unavailable" == projected["reviewer_status"]
        assert projected["ready"] is True
        (candidate,) = cast("list[dict[str, object]]", projected["candidates"])
        assert candidate["manual_eligible"] is True
        assert candidate["observation"] is None
        assert candidate["source_kinds"] == ["hardcover"]
        # The identity stays unverified, so the owner confirms the match and
        # the cover is never selectable and never automatic (#1642).
        assert candidate["match_state"] == "match_not_confirmed"
        assert candidate["selectable"] is False
        assert cast("dict[str, object]", candidate["identity"])["outcome"] not in {
            "verified_manifestation_match",
            "verified_work_match",
        }
        # The cover from the matched book says so, display only: the stored
        # consideration is unchanged and History projects the same note.
        assert candidate["match_reason"] == "matched_book"
        assert candidate["deterministic_rank"] is None
        assert candidate["state"] == "excluded"
        stored = cast("_Store", service._store).current["review_case_" + "e" * 64]
        assert "match_reason" not in stored
        (historical,) = cast(
            "list[dict[str, object]]",
            service.project_historical(stored)["candidates"],
        )
        assert historical["match_reason"] == "matched_book"
        service.validate_decision(
            "review_case_" + "e" * 64,
            projected["consideration_id"],
            "accept",
            {"kind": "cover", "value": candidate["artifact_id"]},
        )


def test_a_listed_edition_cover_is_an_unconfirmed_owner_choice() -> None:
    """A Hardcover edition-list cover is offered, never selectable (#1778).

    It binds by the book's work identity alone, so its identity stays
    `unknown`: the owner may choose it and so confirms the match, and it is
    never selectable and never ranked for an automatic choice.
    """
    from tests.unit.test_cover_identity_listed_edition import (
        _isbn_lane_report,
        _listed_source,
    )

    payload = _preview(11)
    report = _isbn_lane_report(_listed_source(11))
    service = CoverConsiderationService(
        _Store(),
        read_preview=lambda *_: payload,
        read_preview_digest=lambda _: payload,
        clock=lambda: 10,
    )
    case_id = "review_case_" + "c" * 64
    projection = service.ensure(
        case_id, report.to_json(), {"proposal_id": "review_proposal_" + "d" * 64}
    )

    (candidate,) = cast("list[dict[str, Any]]", projection["candidates"])
    assert "unknown" == candidate["identity"]["outcome"]
    assert "match_not_confirmed" == candidate["match_state"]
    assert candidate["manual_eligible"] is True
    assert candidate["selectable"] is False
    assert candidate["deterministic_rank"] is None
    service.validate_decision(
        case_id,
        projection["consideration_id"],
        "accept",
        {"kind": "cover", "value": candidate["artifact_id"]},
    )


def test_a_work_route_gallery_holds_only_the_offered_covers() -> None:
    """A landscape cover and another work's cover are neither shown nor accepted.

    The work-route cover rule (#1642, Q7) bounds the gallery itself, not only
    whether it opens: the owner can choose only what the proposal offers.
    """
    from mechanical_librarian.domain.proposal_set import work_route_cover_sources
    from tests.support.hardcover_work_route import (
        hardcover_work_report,
        work_cover_source,
    )

    service, _document, dispatches, proposal = _audiobook_case()
    payloads = {index: _preview(60 + index) for index in (1, 2, 3)}
    offered = work_cover_source(preview=payloads[1])
    landscape = work_cover_source(900, 500, index=2, preview=payloads[2])
    other_work = work_cover_source(
        work_identity="hardcover-work-0000000099", index=3, preview=payloads[3]
    )
    report = hardcover_work_report(cover_sources=(offered, landscape, other_work))
    assert work_route_cover_sources(report) == (offered,)
    by_digest = {
        cast("CoverArtifact", source.artifact).preview_digest: payloads[index]
        for index, source in ((1, offered), (2, landscape), (3, other_work))
    }
    by_artifact = {
        cast("CoverArtifact", source.artifact).artifact_id: payloads[index]
        for index, source in ((1, offered), (2, landscape), (3, other_work))
    }
    with (
        patch.object(
            service,
            "_read_preview",
            lambda artifact_id, _variant: by_artifact[artifact_id],
        ),
        patch.object(service, "_read_preview_digest", by_digest.__getitem__),
    ):
        projected = service.ensure(
            "review_case_" + "e" * 64, report.to_json(), proposal
        )
        assert [] == dispatches
        (candidate,) = cast("list[dict[str, object]]", projected["candidates"])
        assert (
            candidate["artifact_id"]
            == cast("CoverArtifact", offered.artifact).artifact_id
        )
        for refused in (landscape, other_work):
            with pytest.raises(CoverConsiderationError, match="invalid_choice"):
                service.validate_decision(
                    "review_case_" + "e" * 64,
                    projected["consideration_id"],
                    "accept",
                    {
                        "kind": "cover",
                        "value": cast("CoverArtifact", refused.artifact).artifact_id,
                    },
                )


_LISTED_EDITION = "hardcover-edition-0000000031"


def _edition_cover(designation: CoverDesignation, index: int) -> CoverSource:
    """A square Hardcover cover naming an edition no candidate is."""
    return replace(
        work_cover_source(600, 600, index=index),
        provenance=CoverProvenance("hardcover", designation, WORK, _LISTED_EDITION),
    )


def test_a_work_route_offers_a_listed_edition_cover_of_its_book() -> None:
    """A listed edition binds by its book alone on the work route (#1778).

    Hardcover's audio-edition covers name an edition, never a candidate, so
    the route binds them by the book's work identity, as cover identity does.
    The book must still be an exact work source, and the cover stays an owner
    choice.
    """
    listed = _edition_cover(CoverDesignation.LISTED_EDITION, 1)

    assert (listed,) == work_route_cover_sources(
        hardcover_work_report(cover_sources=(listed,))
    )


def test_a_listed_edition_cover_needs_its_book_to_be_an_exact_work_source() -> None:
    """The book a listed edition names must pass `_supported_work_source`.

    A recording-less report whose book fails it cannot be built with this
    fixture (its lanes are derived from the same target), so the predicate is
    held false here, and each call is checked to name the cover's book.
    """
    listed = _edition_cover(CoverDesignation.LISTED_EDITION, 1)
    report = hardcover_work_report(cover_sources=(listed,))
    asked: list[str] = []

    def unsupported(_report: object, _provider: object, candidate: object) -> bool:
        asked.append(cast("ProviderCandidate", candidate).work_identity)
        return False

    with patch(
        "mechanical_librarian.domain.proposal_set._supported_work_source",
        unsupported,
    ):
        assert () == work_route_cover_sources(report)
    assert [WORK] == asked


@pytest.mark.parametrize(
    "designation",
    (CoverDesignation.EXACT_EDITION, CoverDesignation.RELATED_EDITION),
)
def test_an_edition_cover_naming_no_candidate_is_still_not_offered(
    designation: CoverDesignation,
) -> None:
    edition = _edition_cover(designation, 1)
    listed = _edition_cover(CoverDesignation.LISTED_EDITION, 2)
    report = hardcover_work_report(cover_sources=(edition, listed))

    assert (listed,) == work_route_cover_sources(report)


def test_a_verified_work_route_cover_never_gains_automatic_authority(
    tmp_path,
) -> None:
    """No recording admitted means no automatic cover, whatever its identity.

    The work route opens a cover consideration on an audiobook with no
    admitted recording (#1642). A work-bound cover can still classify as
    verified_work_match (an accepted or associated identifier on its
    candidate). Under the F46 automatic replacement of a tiny incumbent that
    cover must not be admitted automatically, and validation must refuse an
    automatic decision for it (ADR-0073).
    """
    from types import SimpleNamespace

    from mechanical_librarian.adapters import cover_artifacts as decoder
    from mechanical_librarian.adapters.persistence import field_effect_validation
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from mechanical_librarian.adapters.persistence.field_effect_validation import (
        FieldEffectError,
    )
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application import cover_identity
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_audiobook_identity,
    )
    from mechanical_librarian.domain.cover_evidence import (
        CoverDesignation,
        CoverProvenance,
        CoverSource,
        CoverSourceState,
        cover_source_id,
    )
    from mechanical_librarian.domain.proposal_set import (
        audiobook_review_scope,
        work_route_cover_sources,
    )
    from mechanical_librarian.domain.review_case import ReviewCase
    from tests.support.audiobook_reports import publish_audiobook_preview
    from tests.support.cover_images import synthetic_image
    from tests.support.hardcover_work_route import WORK, hardcover_work_report

    originals = [synthetic_image("PNG", size) for size in ((80, 120), (640, 960))]
    decoded = [decoder.decode_cover(payload) for payload in originals]
    incumbent = CoverSource(
        cover_source_id(CoverSourceKind.ABS, "incumbent"),
        CoverSourceKind.ABS,
        CoverSourceState.RETRIEVED,
        decoded[0].artifact,
    )
    work_cover = CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, "hardcover:work:1"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        decoded[1].artifact,
        provenance=CoverProvenance("hardcover", CoverDesignation.DEFAULT, WORK),
    )
    # The book's catalogue also shows the current cover, which confirms it.
    current_cover = CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, "hardcover:work:0"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        decoded[0].artifact,
        provenance=CoverProvenance("hardcover", CoverDesignation.DEFAULT, WORK),
    )
    report = hardcover_work_report(cover_sources=(incumbent, current_cover, work_cover))
    assert not admitted_audiobook_identity(report)
    assert audiobook_review_scope(report) == "work"
    assert work_cover in work_route_cover_sources(report)
    artifacts = CoverArtifactStore(tmp_path / "artifacts")
    for original, item in zip(originals, decoded, strict=True):
        artifacts.stage(item.artifact, original, item.preview)

    # The classifier's verified-work outcome, citing the work's own claim as
    # the identifier evidence an accepted local ISBN family would supply.
    hardcover = next(
        value for value in report.providers if value.provider == "hardcover"
    )
    work_claim = next(
        claim.reference
        for candidate in hardcover.candidates
        if candidate.work_identity == WORK
        for claim in candidate.claims
    )
    verified = (
        "verified_work_match",
        ["accepted_or_associated_work_identifier"],
        [work_claim],
    )
    repository = open_repository(tmp_path / "state.sqlite")
    try:
        with patch.object(
            cover_identity,
            "_classify_sources",
            lambda _report, sources, _level: (
                verified
                if any(source.kind is CoverSourceKind.HARDCOVER for source in sources)
                else ("unknown", ["no_provider_identity"], [])
            ),
        ):
            case_id, _resolved = publish_audiobook_preview(
                repository,
                report,
                ("cover_order",),
                selected_options={"cover_order": "fixed_source_order"},
            )
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            )
            proposal = next(
                row for row in case.proposals if row["destination"] == "cover"
            )
            service = CoverConsiderationService(
                repository,
                read_preview=lambda identity, _variant: artifacts.preview(
                    next(
                        source.artifact
                        for source in report.cover_sources
                        if source.artifact is not None
                        and source.artifact.artifact_id == identity
                    )
                ),
                read_preview_digest=artifacts.read_preview_digest,
                require_artifact=artifacts.require_artifact,
                clock=lambda: 10,
                reviewer_dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("image observer prohibited")
                ),
            )
            projected = service.ensure(case_id, report.to_json(), proposal)

        offered = next(
            row
            for row in cast("list[dict[str, object]]", projected["candidates"])
            if row["incumbent"] is False
        )
        assert cast("dict[str, object]", offered["identity"])["outcome"] == (
            "verified_work_match"
        )
        # Manual choice stays open; selection, which automatic reads, does not.
        assert offered["manual_eligible"] is True
        assert offered["selectable"] is False
        assert offered["match_reason"] == "matched_book"
        assert repository.get_review_case_decisions(case_id) == ()

        # Even a consideration claiming the cover selectable mints nothing.
        document = repository.current_cover_consideration_document(case_id)
        assert document is not None
        forged = json.loads(document)
        policy_report = json.loads(forged["policy_report_document"])
        assert policy_report["owner_ready"] is True
        assert policy_report["actual_target_state"] == "confirmed"
        for row in forged["candidates"]:
            if row["incumbent"] is False:
                row["selectable"] = True
        repository._cover_considerations._admit_automatic_cover(json.dumps(forged), 11)
        assert repository.get_review_case_decisions(case_id) == ()

        # Validation refuses automatic cover authority for this audiobook.
        with pytest.raises(FieldEffectError, match="authority_binding"):
            field_effect_validation._cover_automatic_decision_value(
                repository._connection,
                cast("Any", SimpleNamespace()),
                case,
                report,
                cast("Any", None),
                document,
            )
    finally:
        repository.close()


_PLAN = "effect_plan_" + "b" * 64
_DECISION = "automatic_decision_" + "c" * 64


def _retained_source() -> CoverSource:
    artifact = CoverArtifact.create(
        b"retained-original", _preview(9), "image/png", 400, 600
    )
    return CoverSource(
        cover_source_id(CoverSourceKind.RETAINED, _PLAN),
        CoverSourceKind.RETAINED,
        CoverSourceState.RETRIEVED,
        artifact,
    )


def _retaining_service(
    case: _Case,
    retained: tuple[str, str, str] | None,
    require_retained: Any = None,
) -> tuple[
    CoverConsiderationService,
    list[tuple[str, str, str]],
    list[tuple[str, str, str] | None],
]:
    """The service, the lookups it made, and a cell a test empties to release."""
    asked: list[tuple[str, str, str]] = []
    live = [retained]

    def lookup(connection: str, library: str, item: str):
        asked.append((connection, library, item))
        return live[0]

    service = CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        require_artifact=lambda _artifact: None,
        clock=lambda: case.now,
        retained_original=lookup,
        require_retained=require_retained,
    )
    return service, asked, live


def _with_cover(
    report: MetadataReport,
    state: TargetFieldState,
    sources: tuple[CoverSource, ...],
) -> MetadataReport:
    """``report`` with its cover target in ``state`` over ``sources``."""
    held = next(
        (
            source.artifact.artifact_id
            for source in sources
            if source.kind is CoverSourceKind.ABS and source.artifact is not None
        ),
        None,
    )
    target = report.target
    return MetadataReport.create(
        target=TargetFacts(
            target.connection,
            target.library,
            target.item,
            tuple(
                TargetField(
                    field.destination,
                    state,
                    # A cover the decoder refused still names a value.
                    (held or "current-cover")
                    if state is TargetFieldState.VALUE
                    else None,
                )
                if field.destination is TargetDestination.COVER
                else field
                for field in target.fields
            ),
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        cover_sources=tuple(sorted(sources, key=lambda value: value.source_id)),
    )


def _over_a_cover(sources: tuple[CoverSource, ...]) -> MetadataReport:
    """A report whose current cover is populated, as a switch leaves it."""
    return _with_cover(_report(sources), TargetFieldState.VALUE, sources)


def _retained_report(case: _Case) -> tuple[MetadataReport, tuple[str, str, str]]:
    source = _retained_source()
    assert source.artifact is not None
    return (
        _over_a_cover((*case.sources, source)),
        (source.artifact.original_digest, _PLAN, _DECISION),
    )


def test_a_retained_original_is_an_owner_only_candidate(case: _Case) -> None:
    """Put back's candidate: no identity, no rank, never reviewed (#1363, D2)."""
    report, binding = _retained_report(case)
    service, asked, _ = _retaining_service(case, binding, lambda _digest: None)
    case_id = "review_case_" + "r" * 64

    projected = service.ensure(case_id, report.to_json(), case.proposal)

    target = report.target
    assert (target.connection, target.library, target.item) in asked
    stored = json.loads(case.store.current[case_id])
    assert "retained_original" not in stored
    retained = stored["candidates"][-1]
    assert retained["candidate_kind"] == "retained"
    assert (retained["plan_id"], retained["decision_id"]) == (_PLAN, _DECISION)
    assert [candidate["candidate_kind"] for candidate in stored["candidates"]] == [
        "candidate",
        "candidate",
        "retained",
    ]
    offered = projected["candidates"][-1]
    assert offered["artifact_id"] == "cover_" + binding[0]
    assert offered["identity"] is None
    assert offered["deterministic_rank"] is None
    assert offered["selectable"] is True
    assert offered["manual_eligible"] is True
    assert offered["source_kinds"] == ["retained"]
    assert "plan_id" not in offered and "decision_id" not in offered
    # It never reaches a reviewer: the packet holds only the other covers.
    packet = json.loads(cast(str, stored["packet_document"]))
    assert len(packet["candidates"]) == 2
    service.validate_decision(
        case_id,
        projected["consideration_id"],
        "accept",
        {"kind": "cover", "value": offered["artifact_id"]},
    )
    service.require_owner_cover(case.store.current[case_id], offered["artifact_id"])


def test_a_released_original_can_no_longer_be_put_back(case: _Case) -> None:
    report, binding = _retained_report(case)
    service, _, live = _retaining_service(case, binding, lambda _digest: None)
    case_id = "review_case_" + "u" * 64
    service.ensure(case_id, report.to_json(), case.proposal)
    document = case.store.current[case_id]
    live[0] = None

    offered = service.project_historical(document)["candidates"][-1]

    assert (offered["selectable"], offered["state"]) == (False, "excluded")
    assert offered["manual_eligible"] is False
    with pytest.raises(CoverConsiderationError, match="stale"):
        service.require_owner_cover(document, offered["artifact_id"])


def test_a_retained_original_whose_copy_fails_its_check_is_stale(
    case: _Case,
) -> None:
    report, binding = _retained_report(case)

    def missing(_digest: str) -> None:
        raise ValueError("retained cover unavailable")

    service, _, _ = _retaining_service(case, binding, missing)
    case_id = "review_case_" + "v" * 64
    service.ensure(case_id, report.to_json(), case.proposal)

    with pytest.raises(CoverConsiderationError, match="stale"):
        service.require_owner_cover(case.store.current[case_id], "cover_" + binding[0])


def test_a_retained_original_needs_its_source_in_the_report(case: _Case) -> None:
    """A binding with no retained source in the report offers nothing."""
    _, binding = _retained_report(case)
    service, _, _ = _retaining_service(case, binding)
    case_id = "review_case_" + "w" * 64

    projected = service.ensure(case_id, case.report.to_json(), case.proposal)

    assert "cover_" + binding[0] not in {
        candidate["artifact_id"] for candidate in projected["candidates"]
    }


def test_a_retained_original_that_is_the_current_cover_is_not_offered(
    case: _Case,
) -> None:
    case_id = "review_case_" + "s" * 64
    report = _over_a_cover(case.sources)
    first = case.service.ensure(case_id, report.to_json(), case.proposal)
    incumbent = json.loads(case.store.current[case_id])["incumbent_digest"]
    assert incumbent is not None
    service, _, _ = _retaining_service(case, (incumbent, _PLAN, _DECISION))

    replay = service.ensure(case_id, report.to_json(), case.proposal)

    assert replay["consideration_id"] == first["consideration_id"]
    assert all(
        candidate["candidate_kind"] == "candidate"
        for candidate in json.loads(case.store.current[case_id])["candidates"]
    )


def test_a_retained_original_that_a_provider_also_offers_is_one_card(
    case: _Case,
) -> None:
    """One picture is never two cards: the reviewed candidate stands (#1363)."""
    # A provider's cover, not the current one.
    reviewed = next(
        source
        for source in case.sources
        if source.artifact is not None and source.kind is not CoverSourceKind.ABS
    )
    assert reviewed.artifact is not None
    same = CoverSource(
        cover_source_id(CoverSourceKind.RETAINED, _PLAN),
        CoverSourceKind.RETAINED,
        CoverSourceState.RETRIEVED,
        reviewed.artifact,
    )
    report = _over_a_cover((*case.sources, same))
    binding = (reviewed.artifact.original_digest, _PLAN, _DECISION)
    service, _, _ = _retaining_service(case, binding, lambda _digest: None)
    case_id = "review_case_" + "y" * 64

    projected = service.ensure(case_id, report.to_json(), case.proposal)

    offered = [candidate["artifact_id"] for candidate in projected["candidates"]]
    assert offered.count(reviewed.artifact.artifact_id) == 1
    assert all(
        candidate["candidate_kind"] == "candidate"
        for candidate in json.loads(case.store.current[case_id])["candidates"]
    )


def test_a_retained_original_never_fills_an_empty_cover(case: _Case) -> None:
    """Over an empty cover the proposal withholds the original, so the
    comparison never offers a card its decision would refuse (#1363, D2)."""
    source = _retained_source()
    assert source.artifact is not None
    sources = (
        *(
            candidate
            for candidate in case.sources
            if candidate.kind is not CoverSourceKind.ABS
        ),
        source,
    )
    report = _with_cover(_report(sources), TargetFieldState.MISSING, sources)
    binding = (source.artifact.original_digest, _PLAN, _DECISION)
    service, _, _ = _retaining_service(case, binding, lambda _digest: None)
    case_id = "review_case_" + "e" * 64

    projected = service.ensure(case_id, report.to_json(), case.proposal)

    assert projected["candidates"]
    assert all(
        candidate["candidate_kind"] == "candidate"
        for candidate in projected["candidates"]
    )
    assert all(
        candidate["candidate_kind"] == "candidate"
        for candidate in json.loads(case.store.current[case_id])["candidates"]
    )


_UNDECODABLE_DIGEST = "9" * 64


def _undecodable_retained_report(
    case: _Case,
) -> tuple[MetadataReport, tuple[str, str, str]]:
    """A switch's original the decoder refused, kept by its digest (#1362)."""
    source = CoverSource(
        cover_source_id(CoverSourceKind.RETAINED, _PLAN),
        CoverSourceKind.RETAINED,
        CoverSourceState.DECODE_FAILED,
        original_digest=_UNDECODABLE_DIGEST,
    )
    return (
        _over_a_cover((*case.sources, source)),
        (_UNDECODABLE_DIGEST, _PLAN, _DECISION),
    )


def test_an_undecodable_retained_original_is_a_candidate_with_no_preview(
    case: _Case,
) -> None:
    """Put back of an original the decoder refused (#1362, S11)."""
    report, binding = _undecodable_retained_report(case)
    service, _, _ = _retaining_service(case, binding, lambda _digest: None)
    case_id = "review_case_" + "n" * 64

    projected = service.ensure(case_id, report.to_json(), case.proposal)

    document = case.store.current[case_id]
    retained = json.loads(document)["candidates"][-1]
    assert retained["candidate_kind"] == "retained"
    assert (retained["artifact_id"], retained["original_sha256"]) == (
        "cover_" + _UNDECODABLE_DIGEST,
        _UNDECODABLE_DIGEST,
    )
    assert (
        retained["height"],
        retained["width"],
        retained["preview_sha256"],
        retained["transport_sha256"],
    ) == (None, None, None, None)
    # It is a card of its own, never an omission a reviewer or the page
    # would read as a cover not shown (#1362, S18).
    retained_reference = retained["reference"]
    assert all(
        omission["reference"] != retained_reference
        for omission in [
            *json.loads(document)["omissions"],
            *json.loads(cast(str, json.loads(document)["packet_document"]))[
                "omissions"
            ],
            *cast("list[dict[str, object]]", projected["omissions"]),
        ]
    )
    # The stored document rebuilds exactly, so it reads back unchanged.
    offered = service.project_historical(document)["candidates"][-1]
    assert offered == projected["candidates"][-1]
    assert (offered["selectable"], offered["manual_eligible"]) == (True, True)
    with pytest.raises(CoverConsiderationError, match="not_found"):
        service.image(
            case_id, str(projected["consideration_id"]), str(offered["reference"])
        )
    service.validate_decision(
        case_id,
        projected["consideration_id"],
        "accept",
        {"kind": "cover", "value": offered["artifact_id"]},
    )
    service.require_owner_cover(document, offered["artifact_id"])


def test_an_undecodable_retained_original_with_nothing_to_review_is_no_omission(
    case: _Case,
) -> None:
    """With no readable cover at all there is no packet; the original is
    still offered and is still not named as a cover not shown (#1362)."""
    current = CoverSource(
        cover_source_id(CoverSourceKind.ABS, "current"),
        CoverSourceKind.ABS,
        CoverSourceState.DECODE_FAILED,
        original_digest="7" * 64,
    )
    retained = CoverSource(
        cover_source_id(CoverSourceKind.RETAINED, _PLAN),
        CoverSourceKind.RETAINED,
        CoverSourceState.DECODE_FAILED,
        original_digest=_UNDECODABLE_DIGEST,
    )
    report = _with_cover(
        _report((current, retained)), TargetFieldState.VALUE, (current, retained)
    )
    service, _, _ = _retaining_service(
        case, (_UNDECODABLE_DIGEST, _PLAN, _DECISION), lambda _digest: None
    )
    case_id = "review_case_" + "p" * 64

    projected = service.ensure(case_id, report.to_json(), case.proposal)

    stored = json.loads(case.store.current[case_id])
    assert stored["packet_document"] is None
    assert [item["candidate_kind"] for item in stored["candidates"]] == ["retained"]
    assert [omission["reference"] for omission in projected["omissions"]] == [
        cover_source_reference(report.report_id, current.source_id)
    ]
    service.project_historical(case.store.current[case_id])


@pytest.mark.parametrize(
    "tamper",
    (
        pytest.param({"width": 400}, id="width"),
        pytest.param({"preview_sha256": "a" * 64}, id="preview"),
        pytest.param({"original_sha256": "8" * 64}, id="digest"),
        pytest.param({"artifact_id": "cover_" + "8" * 64}, id="artifact_id"),
    ),
)
def test_a_tampered_undecodable_retained_candidate_is_refused(
    case: _Case, tamper: dict[str, object]
) -> None:
    report, binding = _undecodable_retained_report(case)
    service, _, _ = _retaining_service(case, binding)
    case_id = "review_case_" + "o" * 64
    service.ensure(case_id, report.to_json(), case.proposal)
    document = json.loads(case.store.current[case_id])
    document["candidates"][-1] = {**document["candidates"][-1], **tamper}

    with pytest.raises(ValueError, match="cover consideration"):
        service.project_historical(json.dumps(document))


def test_an_undecodable_retained_original_that_is_the_current_cover_is_not_offered(
    case: _Case,
) -> None:
    current = CoverSource(
        cover_source_id(CoverSourceKind.ABS, "current"),
        CoverSourceKind.ABS,
        CoverSourceState.DECODE_FAILED,
        original_digest=_UNDECODABLE_DIGEST,
    )
    retained = CoverSource(
        cover_source_id(CoverSourceKind.RETAINED, _PLAN),
        CoverSourceKind.RETAINED,
        CoverSourceState.DECODE_FAILED,
        original_digest=_UNDECODABLE_DIGEST,
    )
    sources = (
        *(source for source in case.sources if source.kind is not CoverSourceKind.ABS),
        current,
        retained,
    )
    report = _with_cover(_report(sources), TargetFieldState.VALUE, sources)
    service, _, _ = _retaining_service(
        case, (_UNDECODABLE_DIGEST, _PLAN, _DECISION), lambda _digest: None
    )
    case_id = "review_case_" + "q" * 64

    service.ensure(case_id, report.to_json(), case.proposal)

    assert all(
        candidate["candidate_kind"] == "candidate"
        for candidate in json.loads(case.store.current[case_id])["candidates"]
    )


@pytest.mark.parametrize(
    "tamper",
    (
        pytest.param({"width": 1}, id="width"),
        pytest.param({"plan_id": "plan"}, id="bad_plan"),
        pytest.param({"identity": {}}, id="identity"),
        pytest.param({"extra": 1}, id="extra_key"),
    ),
)
def test_a_malformed_retained_candidate_is_refused(
    case: _Case, tamper: dict[str, object]
) -> None:
    report, binding = _retained_report(case)
    service, _, _ = _retaining_service(case, binding)
    case_id = "review_case_" + "t" * 64
    service.ensure(case_id, report.to_json(), case.proposal)
    document = json.loads(case.store.current[case_id])
    document["candidates"][-1] = {**document["candidates"][-1], **tamper}

    with pytest.raises(ValueError, match="cover consideration"):
        service.project_historical(json.dumps(document))


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        ("malformed", "unavailable"),
        ("oversized", "unavailable"),
        ("missing", "absent"),
    ),
)
def test_a_held_cover_that_could_not_be_read_is_an_unavailable_current_cover(
    case: _Case, state: str, expected: str
) -> None:
    """The book page names it "Image unavailable", as Covers does (#1363, 7.1)."""
    from mechanical_librarian.application.cover_consideration import (
        _cover_incumbent,
    )
    from mechanical_librarian.domain.metadata_report import (
        TargetDestination,
        TargetFacts,
        TargetField,
        TargetFieldState,
    )

    target = case.report.target
    report = MetadataReport.create(
        target=TargetFacts(
            target.connection,
            target.library,
            target.item,
            tuple(
                TargetField(field.destination, TargetFieldState(state))
                if field.destination is TargetDestination.COVER
                else field
                for field in target.fields
            ),
        ),
        file=case.report.file,
        local_claims=case.report.local_claims,
        local_reasons=case.report.local_reasons,
        providers=case.report.providers,
        cover_sources=(),
    )

    incumbent, digest = _cover_incumbent(report, [])

    assert (incumbent["state"], incumbent["reference"], digest) == (
        expected,
        None,
        None,
    )


def _policy_over(tmp_path, policy: CoverPolicy = CoverPolicy(), **options: Any):
    """The policy report a consideration records over a qualified cover report."""
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from tests.support.cover_producers import _qualified_cover_report

    report, _ = _qualified_cover_report(tmp_path, **options)
    artifacts = CoverArtifactStore(tmp_path / "artifacts")
    service = CoverConsiderationService(
        _Store(),
        read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
            next(
                source.artifact.preview_digest
                for source in report.cover_sources
                if source.artifact is not None
                and source.artifact.artifact_id == artifact_id
            )
        ),
        read_preview_digest=artifacts.read_preview_digest,
        clock=lambda: 10,
        policy=policy,
    )
    projection = service.ensure(
        "review_case_" + "e" * 64,
        report.to_json(),
        {"proposal_id": "review_proposal_" + "f" * 64},
    )
    return cast("dict[str, Any]", projection["policy"])


_DIGEST = hashlib.sha256(b"refused bytes").hexdigest()


@pytest.mark.parametrize(
    "options",
    [
        {"abs_state": CoverSourceState.TIMEOUT},
        {"abs_state": CoverSourceState.UNAVAILABLE},
        {"abs_state": CoverSourceState.SOURCE_CHANGED},
        {"abs_state": CoverSourceState.REJECTED},
        {},
        {"target_state": TargetFieldState.MALFORMED},
        {"target_state": TargetFieldState.OVERSIZED},
        {"target_state": TargetFieldState.UNLOADED},
        {
            "target_state": TargetFieldState.MALFORMED,
            "abs_state": CoverSourceState.DECODE_FAILED,
            "abs_digest": _DIGEST,
        },
    ],
    ids=[
        "timeout",
        "unavailable",
        "source_changed",
        "other_state",
        "no_abs_source",
        "malformed_target",
        "oversized_target",
        "unloaded_target",
        "refused_over_malformed_target",
    ],
)
def test_a_read_that_settled_nothing_keeps_the_current_cover(
    tmp_path, options: dict[str, object]
) -> None:
    """ADR-0094 S2 and S3: an unknown read decides nothing, in every mode."""
    for index, protection in enumerate(("protected", "unprotected", "proposals_only")):
        policy = _policy_over(
            tmp_path / str(index),
            CoverPolicy(current_protection=protection),
            readable_current=False,
            **options,
        )
        assert policy["stage"]["outcome"] == "keep"
        assert policy["stage"]["issues"] == []
        assert policy["stage"]["choice"] is None
        assert policy["recommended_candidate_reference"] is None
        assert policy["reasons"] == ["current_cover_kept"]
        assert policy["actual_target_state"] == "unknown"


@pytest.mark.parametrize(
    ("options", "outcome"),
    [
        (
            {"abs_state": CoverSourceState.DECODE_FAILED, "abs_digest": _DIGEST},
            "replace",
        ),
        (
            {
                "abs_state": CoverSourceState.ANIMATED_OR_MULTIFRAME,
                "abs_digest": _DIGEST,
            },
            "replace",
        ),
        ({"abs_state": CoverSourceState.OVERSIZED, "abs_digest": _DIGEST}, "replace"),
        (
            {"abs_state": CoverSourceState.UNSUPPORTED_FORMAT, "abs_digest": _DIGEST},
            "replace",
        ),
        ({"abs_state": CoverSourceState.ABSENT}, "replace"),
        ({"abs_state": CoverSourceState.DECODE_FAILED}, "suggest"),
        ({"abs_state": CoverSourceState.OVERSIZED}, "suggest"),
        ({"abs_state": CoverSourceState.UNSUPPORTED_FORMAT}, "suggest"),
    ],
    ids=[
        "decode_failed_bound",
        "animated_bound",
        "oversized_bound",
        "unsupported_bound",
        "absent_404",
        "decode_failed_unbound",
        "oversized_unbound",
        "unsupported_unbound",
    ],
)
def test_a_terminal_unreadable_read_is_switched_only_when_it_can_be_kept(
    tmp_path, options: dict[str, object], outcome: str
) -> None:
    """ADR-0094 S2 and S4: bound bytes or a 404 may switch; anything else suggests."""
    policy = _policy_over(tmp_path, readable_current=False, **options)

    assert policy["stage"]["outcome"] == outcome
    assert policy["stage"]["reason"] == "unreadable"
    assert policy["stage"]["issues"] == ["unreadable"]
    assert policy["stage"]["choice"] is not None
    assert policy["recommended_candidate_reference"] == policy["stage"]["choice"]
    assert policy["actual_target_state"] == "unknown"

    proposals_only = _policy_over(
        tmp_path / "proposals_only",
        CoverPolicy(current_protection="proposals_only"),
        readable_current=False,
        **options,
    )
    assert proposals_only["stage"]["outcome"] == "suggest"


@pytest.mark.parametrize(
    ("identity", "outcome"),
    [
        (("conflict", ("edition_work_contradiction",)), "hold"),
        (("unknown", ("retained_creator_or_contributor_disagreement",)), "hold"),
        (("unknown", ("work_identity_level_refused",)), "keep"),
        (("plausible_match", ()), "keep"),
    ],
    ids=["conflict", "creator_disagreement", "level_refused", "plausible_match"],
)
def test_a_current_cover_whose_sources_name_another_book_is_held(
    tmp_path, identity: tuple[str, tuple[str, ...]], outcome: str
) -> None:
    """ADR-0094 S6: a positive contradiction holds the field; nothing else does."""
    from tests.support.cover_producers import incumbent_identity

    for index, protection in enumerate(("protected", "unprotected", "proposals_only")):
        with incumbent_identity(*identity):
            # A square ebook cover has a fault, which the hold overrides.
            policy = _policy_over(
                tmp_path / str(index),
                CoverPolicy(current_protection=protection),
                incumbent_size=(500, 500),
            )
        held = outcome == "hold"
        assert policy["stage"]["outcome"] == (
            "hold" if held else "suggest" if index == 2 else "replace"
        )
        assert policy["stage"]["issues"] == (
            ["current_contradicted"] if held else ["wrong_shape"]
        )
        assert policy["stage"]["reason"] == (
            "current_contradicted" if held else "wrong_shape"
        )
        assert (policy["recommended_candidate_reference"] is None) is held
        assert policy["actual_target_state"] == "unknown"

    with incumbent_identity(*identity):
        fault_free = _policy_over(tmp_path / "fault_free")
    assert fault_free["stage"]["outcome"] == outcome
    assert fault_free["stage"]["issues"] == (
        ["current_contradicted"] if outcome == "hold" else []
    )
