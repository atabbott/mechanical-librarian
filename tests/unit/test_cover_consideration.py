from __future__ import annotations

import hashlib
import json
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.application import cover_observation
from mechanical_librarian.application.cover_consideration import (
    CoverConsideration,
    CoverConsiderationError,
    CoverConsiderationService,
    ReviewRunner,
    cover_consideration_currentness,
    cover_consideration_is_current,
    cover_consideration_replay_digest,
)
from mechanical_librarian.application.cover_packet_semantics import (
    canonical_packet_json,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.review_web import _needs_attention
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
)
from mechanical_librarian.application.reviewer_contracts import ReviewerDispatch
from mechanical_librarian.domain.cover_evidence import CoverSourceKind
from mechanical_librarian.domain.metadata_report import MetadataReport
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


def test_incumbent_only_cover_remains_an_explicit_owner_choice() -> None:
    assert _needs_attention(
        {
            "destination": "cover",
            "manual_required": False,
            "result": {
                "outcome": "keep",
                "reasons": ["current_cover_verified"],
            },
        }
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
        "prefer_minimal_composition": True,
        "schema_version": "cover-policy-v1",
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
        bind_policy=lambda policy, replay: bindings.append((policy, replay)),
    )
    changed = CoverPolicy(True, True, True)

    with service.policy_update(changed):
        pass
    value = service.ensure(
        "review_case_" + "9" * 64, case.report.to_json(), case.proposal
    )

    assert value["policy_preferences"] == json.loads(changed.document)
    assert bindings == [(changed.reference, service.replay_reference)]

    previous_replay = service.replay_reference
    service._bind_policy = lambda _policy, _replay: (_ for _ in ()).throw(
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
        "cover_replay_1bb7c8633829342bff0bf0677cb5499269449e42151854fdc190bcd658688130"
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
        "cover_replay_627bdd7d96e8d7e27f26216d203c6d6ba35ec59bd87fff670382cd9f3dda2240"
    )
    assert (
        replayed_qualified.ensure(
            qualified_case_id, case.report.to_json(), case.proposal
        )["consideration_id"]
        == first_qualified["consideration_id"]
    )
    assert len(dispatches) == 1


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
    assert different_document["schema_version"] == "cover-consideration-v4"
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
        payload = case.service.image(
            case_id, consideration_id, str(candidate["reference"])
        )
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


def test_an_audiobook_consideration_dispatches_no_observer(
    case: _Case,
) -> None:
    service, document, dispatches, proposal = _audiobook_case()

    projected = service.ensure("review_case_" + "e" * 64, document, proposal)

    assert [] == dispatches
    assert "unavailable" == projected["reviewer_status"]
    assert [] == projected["candidates"]
    persisted = json.loads(
        cast("_Store", service._store).current["review_case_" + "e" * 64]
    )
    assert persisted["packet_document"] is None
    assert persisted["observation_document"] is None
    assert persisted["identity_document"] is None
    assert persisted["policy_report_document"] is None
    # The same evidence on the ebook path still packets and still observes.
    assert case.service.ensure(
        "review_case_" + "f" * 64, case.report.to_json(), case.proposal
    )["candidates"]
