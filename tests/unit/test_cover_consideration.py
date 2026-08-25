from __future__ import annotations

import hashlib
import json
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.application import cover_observation
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationError,
    CoverConsiderationService,
    ReviewRunner,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.review_web import _needs_attention
from tests.unit.test_cover_packets import (
    _observer_result,
    _preview,
    _report,
    _source,
)


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
