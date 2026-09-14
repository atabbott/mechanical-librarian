"""Immutable automatic cover authority bound to one exact consideration."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from mechanical_librarian.domain.audiobook_decision_qualification import (
    F46_IDENTITIES_BY_DESTINATION,
    qualification_manifest_sha256,
)
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
)
from mechanical_librarian.domain.cover_qualification_policy import (
    COVER_ELECTION_QUALIFICATION_MANIFEST_SHA256,
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CoverAutomaticDecision:
    decision_id: str
    review_case_id: str
    report_id: str
    proposal_set_id: str
    proposal_id: str
    target_fingerprint: str
    candidate_id: str
    resolved_policy_id: str
    policy_generation: int
    consideration_id: str
    consideration_digest: str
    replay_digest: str
    policy_reference: str
    candidate_reference: str
    artifact_id: str
    rank_key: tuple[int, int, int, int, int, int, str]
    citation_ids: tuple[str, ...]
    supersedes_decision_id: str | None
    idempotency_digest: str
    occurred_at: int
    qualification_identity: str | None = None

    destination = "cover"
    outcome = "candidate"
    policy_category = "cover_order"

    @property
    def policy_option(self) -> str:
        return (
            "fixed_source_order"
            if self.qualification_identity is None
            else self.qualification_identity.split(":", 1)[1]
        )

    @property
    def qualification_manifest_sha256(self) -> str:
        return (
            COVER_ELECTION_QUALIFICATION_MANIFEST_SHA256
            if self.qualification_identity is None
            else qualification_manifest_sha256(self.qualification_identity)
        )

    bundle_id = None

    @property
    def source_outcome_id(self) -> str:
        return self.consideration_id

    @property
    def rule_id(self) -> str:
        return "cover_rule_" + _digest(
            {
                "manifest": self.qualification_manifest_sha256,
                "policy": self.policy_option,
            }
        )

    @classmethod
    def create(
        cls,
        *,
        review_case_id: str,
        report_id: str,
        proposal_set_id: str,
        proposal_id: str,
        target_fingerprint: str,
        candidate_id: str,
        resolved_policy_id: str,
        policy_generation: int,
        consideration_id: str,
        consideration_digest: str,
        replay_digest: str,
        policy_reference: str,
        candidate_reference: str,
        artifact_id: str,
        rank_key: tuple[int, int, int, int, int, int, str],
        citation_ids: tuple[str, ...],
        supersedes_decision_id: str | None,
        occurred_at: int,
        qualification_identity: str | None = None,
    ) -> "CoverAutomaticDecision":
        core: dict[str, object] = {
            "qualification_identity": qualification_identity,
            "artifact_id": artifact_id,
            "candidate_id": candidate_id,
            "candidate_reference": candidate_reference,
            "citation_ids": list(citation_ids),
            "consideration_digest": consideration_digest,
            "consideration_id": consideration_id,
            "policy_generation": policy_generation,
            "policy_reference": policy_reference,
            "proposal_id": proposal_id,
            "proposal_set_id": proposal_set_id,
            "rank_key": list(rank_key),
            "replay_digest": replay_digest,
            "report_id": report_id,
            "resolved_policy_id": resolved_policy_id,
            "review_case_id": review_case_id,
            "supersedes_decision_id": supersedes_decision_id,
            "target_fingerprint": target_fingerprint,
        }
        return cls(
            decision_id="automatic_decision_"
            + _digest({**core, "occurred_at": occurred_at}),
            review_case_id=review_case_id,
            report_id=report_id,
            proposal_set_id=proposal_set_id,
            proposal_id=proposal_id,
            target_fingerprint=target_fingerprint,
            candidate_id=candidate_id,
            resolved_policy_id=resolved_policy_id,
            policy_generation=policy_generation,
            consideration_id=consideration_id,
            consideration_digest=consideration_digest,
            replay_digest=replay_digest,
            policy_reference=policy_reference,
            candidate_reference=candidate_reference,
            artifact_id=artifact_id,
            rank_key=rank_key,
            citation_ids=citation_ids,
            supersedes_decision_id=supersedes_decision_id,
            idempotency_digest=_digest(core),
            occurred_at=occurred_at,
            qualification_identity=qualification_identity,
        )

    def __post_init__(self) -> None:
        def identifier(prefix: str, value: object) -> bool:
            return (
                type(value) is str
                and re.fullmatch(prefix + r"_[0-9a-f]{64}", value) is not None
            )

        if (
            not identifier("automatic_decision", self.decision_id)
            or not identifier("review_case", self.review_case_id)
            or not identifier("report", self.report_id)
            or not identifier("proposal_set", self.proposal_set_id)
            or not identifier("review_proposal", self.proposal_id)
            or not identifier("alternative", self.candidate_id)
            or not identifier("cover_consideration", self.consideration_id)
            or not identifier("cover_ref", self.candidate_reference)
            or not identifier("cover", self.artifact_id)
            or self.supersedes_decision_id is not None
            and not identifier("automatic_decision", self.supersedes_decision_id)
            or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", self.resolved_policy_id)
            is None
            or any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (
                    self.target_fingerprint,
                    self.consideration_digest,
                    self.replay_digest,
                    self.idempotency_digest,
                )
            )
            or not identifier("cover_policy", self.policy_reference)
            or type(self.policy_generation) is not int
            or self.policy_generation < 1
            or type(self.occurred_at) is not int
            or self.occurred_at < 0
            or len(self.rank_key) != 7
            or any(type(value) is not int for value in self.rank_key[:6])
            or self.rank_key[6] != self.candidate_reference
            or not self.citation_ids
            or self.citation_ids != tuple(sorted(set(self.citation_ids)))
        ):
            raise AutomaticDecisionError("invalid")
        if self.qualification_identity is not None:
            if (
                self.qualification_identity
                not in F46_IDENTITIES_BY_DESTINATION["cover"]
            ):
                raise AutomaticDecisionError("invalid")
            qualification_manifest_sha256(self.qualification_identity)
        core = self._core()
        if self.idempotency_digest != _digest(core) or self.decision_id != (
            "automatic_decision_" + _digest({**core, "occurred_at": self.occurred_at})
        ):
            raise AutomaticDecisionError("invalid")

    def _core(self) -> dict[str, object]:
        return {
            "qualification_identity": self.qualification_identity,
            "artifact_id": self.artifact_id,
            "candidate_id": self.candidate_id,
            "candidate_reference": self.candidate_reference,
            "citation_ids": list(self.citation_ids),
            "consideration_digest": self.consideration_digest,
            "consideration_id": self.consideration_id,
            "policy_generation": self.policy_generation,
            "policy_reference": self.policy_reference,
            "proposal_id": self.proposal_id,
            "proposal_set_id": self.proposal_set_id,
            "rank_key": list(self.rank_key),
            "replay_digest": self.replay_digest,
            "report_id": self.report_id,
            "resolved_policy_id": self.resolved_policy_id,
            "review_case_id": self.review_case_id,
            "supersedes_decision_id": self.supersedes_decision_id,
            "target_fingerprint": self.target_fingerprint,
        }

    def to_json(self) -> str:
        return _canonical(
            {
                "decision_id": self.decision_id,
                **self._core(),
                "destination": self.destination,
                "idempotency_digest": self.idempotency_digest,
                "occurred_at": self.occurred_at,
                "origin": "automatic_cover_policy",
                "outcome": self.outcome,
                "policy_category": self.policy_category,
                "policy_option": self.policy_option,
                "qualification_manifest_sha256": self.qualification_manifest_sha256,
                "schema_version": "cover-automatic-decision-v2",
            }
        )

    @classmethod
    def from_json(cls, document: str) -> "CoverAutomaticDecision":
        try:
            raw = json.loads(document)
            if raw.get("schema_version") != "cover-automatic-decision-v2":
                raise AutomaticDecisionError("invalid")
            values = {
                key: raw[key]
                for key in cls.__dataclass_fields__
                if key not in {"rank_key", "citation_ids"}
            }
            values["rank_key"] = tuple(raw["rank_key"])
            values["citation_ids"] = tuple(raw["citation_ids"])
            result = cls(**values)
            if result.to_json() != document:
                raise AutomaticDecisionError("invalid")
            return result
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AutomaticDecisionError("invalid") from error


AutomaticAuthority = AutomaticDecision | CoverAutomaticDecision


def parse_automatic_authority(document: str) -> AutomaticAuthority:
    try:
        raw = json.loads(document)
    except (TypeError, json.JSONDecodeError) as error:
        raise AutomaticDecisionError("invalid") from error
    if type(raw) is dict and raw.get("schema_version") == "cover-automatic-decision-v2":
        return CoverAutomaticDecision.from_json(document)
    return AutomaticDecision.from_json(document)
