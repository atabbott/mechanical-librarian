from __future__ import annotations

import json
import unittest

from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationError,
    CoverConsiderationService,
)
from mechanical_librarian.application.review_web import _needs_attention
from mechanical_librarian.application.cover_policy import CoverPolicy
from tests.unit.test_cover_packets import _preview, _report, _source


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
            self.events.append({"kind": "superseded", "occurred_at": values["occurred_at"]})
        self.current[review_case_id] = document
        self.events.append({"kind": values.get("preparation_kind", "automatic_prepared"), "occurred_at": values["occurred_at"]})
        return document

    def record_cover_consideration_outcome(self, consideration_id, kind, occurred_at, decision_id=None):
        self.events.append({"consideration_id": consideration_id, "decision_id": decision_id, "kind": kind, "occurred_at": occurred_at})

    def cover_consideration_history(self, review_case_id, limit=50):
        del review_case_id
        return tuple(reversed(self.events[-limit:]))


class CoverConsiderationTests(unittest.TestCase):
    def setUp(self) -> None:
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

    def test_model_free_manual_path_is_persisted_before_exact_selection(self) -> None:
        value = self.service.ensure(
            "review_case_" + "b" * 64,
            self.report.to_json(),
            self.proposal,
        )
        self.assertEqual(value["reviewer_status"], "unavailable")
        self.assertTrue(value["candidates"][0]["incumbent"])
        alternative = value["candidates"][1]
        self.assertTrue(alternative["selectable"])
        self.service.validate_decision(
            "review_case_" + "b" * 64,
            value["consideration_id"],
            "accept",
            {"kind": "cover", "value": alternative["artifact_id"]},
        )
        with self.assertRaisesRegex(CoverConsiderationError, "invalid_choice"):
            self.service.validate_decision(
                "review_case_" + "b" * 64,
                value["consideration_id"],
                "accept",
                {"kind": "cover", "value": value["candidates"][0]["artifact_id"]},
            )

    def test_incumbent_only_cover_remains_an_explicit_owner_choice(self) -> None:
        self.assertTrue(
            _needs_attention(
                {
                    "destination": "cover",
                    "manual_required": False,
                    "result": {
                        "outcome": "keep",
                        "reasons": ["current_cover_verified"],
                    },
                }
            )
        )

    def test_identical_replay_reopens_and_explicit_retry_creates_successor(self) -> None:
        case_id = "review_case_" + "c" * 64
        first = self.service.ensure(case_id, self.report.to_json(), self.proposal)
        replay = self.service.ensure(case_id, self.report.to_json(), self.proposal)
        self.assertEqual(replay["consideration_id"], first["consideration_id"])
        self.now += 1
        successor = self.service.ensure(
            case_id, self.report.to_json(), self.proposal, retry=True
        )
        self.assertEqual(successor["supersedes_id"], first["consideration_id"])
        self.assertEqual([event["kind"] for event in self.store.events], ["automatic_prepared", "superseded", "owner_retry_prepared"])

    def test_incumbent_duplicate_remains_bound_to_abs_original(self) -> None:
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
            self.proposal,
        )

        self.assertEqual(len(value["candidates"]), 1)
        self.assertTrue(value["candidates"][0]["incumbent"])
        self.assertEqual(
            value["candidates"][0]["artifact_id"],
            incumbent.artifact.artifact_id,
        )
        self.assertEqual(
            json.loads(store.current["review_case_" + "d" * 64])[
                "incumbent_digest"
            ],
            incumbent.artifact.original_digest,
        )

    def test_exact_policy_preferences_are_persisted_and_projected(self) -> None:
        service = CoverConsiderationService(
            self.store,
            read_preview=lambda artifact, _variant: self.payload_by_artifact[artifact],
            read_preview_digest=lambda digest: self.payload_by_preview[digest],
            clock=lambda: self.now,
            policy=CoverPolicy(True, True, True),
        )
        case_id = "review_case_" + "e" * 64

        value = service.ensure(case_id, self.report.to_json(), self.proposal)

        self.assertEqual(
            value["policy_preferences"],
            {
                "avoid_promotional_text": True,
                "avoid_tie_in_branding": True,
                "prefer_minimal_composition": True,
                "schema_version": "cover-policy-v1",
            },
        )
        persisted = json.loads(self.store.current[case_id])
        self.assertEqual(
            json.loads(persisted["policy_document"]),
            value["policy_preferences"],
        )
        self.assertEqual(persisted["policy_reference"], value["policy_reference"])

    def test_changed_reviewer_contract_makes_the_prior_consideration_noncurrent(self) -> None:
        case_id = "review_case_" + "f" * 64
        first = self.service.ensure(case_id, self.report.to_json(), self.proposal)
        changed = CoverConsiderationService(
            self.store,
            read_preview=lambda artifact, _variant: self.payload_by_artifact[artifact],
            read_preview_digest=lambda digest: self.payload_by_preview[digest],
            clock=lambda: self.now + 1,
            reviewer_profile=b"changed-profile",
        )

        self.assertIsNone(changed.project(self.store.current[case_id]))
        with self.assertRaisesRegex(CoverConsiderationError, "stale"):
            changed.validate_decision(
                case_id,
                str(first["consideration_id"]),
                "keep_current",
                None,
            )
        successor = changed.ensure(case_id, self.report.to_json(), self.proposal)
        self.assertEqual(first["consideration_id"], successor["supersedes_id"])

    def test_preview_store_failure_is_typed_unavailable(self) -> None:
        case_id = "review_case_" + "1" * 64
        value = self.service.ensure(case_id, self.report.to_json(), self.proposal)

        def unavailable(_digest: str) -> bytes:
            raise RuntimeError("synthetic store failure")

        broken = CoverConsiderationService(
            self.store,
            read_preview=lambda artifact, _variant: self.payload_by_artifact[artifact],
            read_preview_digest=unavailable,
            clock=lambda: self.now,
        )
        candidate = value["candidates"][0]
        with self.assertRaisesRegex(CoverConsiderationError, "unavailable"):
            broken.image(
                case_id,
                str(value["consideration_id"]),
                str(candidate["reference"]),
            )


if __name__ == "__main__":
    unittest.main()
