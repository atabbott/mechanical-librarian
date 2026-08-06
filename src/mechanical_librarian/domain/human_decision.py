"""Immutable, exact-version human decisions for ordinary review fields."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Self

from mechanical_librarian.domain.review_case import ReviewCase


_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_OUTCOMES = {"accept", "keep_current", "reject"}


class HumanDecisionError(ValueError):
    """A fixed, value-free decision validation or state failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class HumanDecision:
    decision_id: str
    review_case_id: str
    proposal_id: str
    target_fingerprint: str
    destination: str
    actor: str
    outcome: str
    selected_value: dict[str, object] | None
    idempotency_digest: str
    request_digest: str
    occurred_at: int

    @staticmethod
    def idempotency_digest_for(idempotency_key: str) -> str:
        if (
            type(idempotency_key) is not str
            or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
        ):
            raise HumanDecisionError("invalid")
        return _sha256(idempotency_key)

    def matches_request(
        self,
        *,
        review_case_id: str,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        actor: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_key: str,
    ) -> bool:
        return (
            self.review_case_id == review_case_id
            and self.proposal_id == proposal_id
            and self.target_fingerprint == target_fingerprint
            and self.destination == destination
            and self.actor == actor
            and self.outcome == outcome
            and self.selected_value == selected_value
            and self.idempotency_digest
            == self.idempotency_digest_for(idempotency_key)
        )

    @classmethod
    def create(
        cls,
        case: ReviewCase,
        *,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        actor: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_key: str,
        occurred_at: int,
    ) -> Self:
        if (
            actor != "owner"
            or outcome not in _OUTCOMES
            or type(occurred_at) is not int
            or occurred_at < 0
            or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
            or target_fingerprint != case.target_fingerprint
        ):
            raise HumanDecisionError("invalid")
        proposal = next(
            (
                value
                for value in case.proposals
                if value["destination"] == destination
                and value["proposal_id"] == proposal_id
            ),
            None,
        )
        if proposal is None:
            raise HumanDecisionError("stale")
        result = proposal["result"]
        if type(result) is not dict:
            raise HumanDecisionError("invalid")
        actionable = (
            result.get("outcome") == "unresolved_choice"
            or proposal["manual_required"] is True
        )
        if not actionable:
            raise HumanDecisionError("unsupported")
        if outcome == "accept":
            alternatives = result.get("alternatives")
            if (
                type(selected_value) is not dict
                or type(alternatives) is not list
                or selected_value
                not in [
                    alternative.get("value")
                    for alternative in alternatives
                    if type(alternative) is dict
                ]
            ):
                raise HumanDecisionError("invalid_selection")
        elif selected_value is not None:
            raise HumanDecisionError("invalid_selection")
        idempotency_digest = cls.idempotency_digest_for(idempotency_key)
        request = {
            "actor": actor,
            "destination": destination,
            "idempotency_digest": idempotency_digest,
            "outcome": outcome,
            "proposal_id": proposal_id,
            "review_case_id": case.review_case_id,
            "selected_value": selected_value,
            "target_fingerprint": target_fingerprint,
        }
        request_digest = _sha256(_canonical(request))
        decision_id = "human_decision_" + _sha256(
            _canonical({"request_digest": request_digest, "occurred_at": occurred_at})
        )
        value = cls(
            decision_id,
            case.review_case_id,
            proposal_id,
            target_fingerprint,
            destination,
            actor,
            outcome,
            selected_value,
            idempotency_digest,
            request_digest,
            occurred_at,
        )
        value.validate_for(case)
        return value

    def validate_for(self, case: ReviewCase) -> None:
        proposal = next(
            (
                value
                for value in case.proposals
                if value["destination"] == self.destination
                and value["proposal_id"] == self.proposal_id
            ),
            None,
        )
        if (
            self.review_case_id != case.review_case_id
            or self.target_fingerprint != case.target_fingerprint
            or proposal is None
        ):
            raise HumanDecisionError("stale")
        result = proposal["result"]
        if type(result) is not dict or not (
            result.get("outcome") == "unresolved_choice"
            or proposal["manual_required"] is True
        ):
            raise HumanDecisionError("unsupported")
        alternatives = result.get("alternatives")
        if self.outcome == "accept":
            if (
                type(self.selected_value) is not dict
                or type(alternatives) is not list
                or self.selected_value
                not in [
                    alternative.get("value")
                    for alternative in alternatives
                    if type(alternative) is dict
                ]
            ):
                raise HumanDecisionError("invalid_selection")
        elif self.selected_value is not None:
            raise HumanDecisionError("invalid_selection")
        request = {
            "actor": self.actor,
            "destination": self.destination,
            "idempotency_digest": self.idempotency_digest,
            "outcome": self.outcome,
            "proposal_id": self.proposal_id,
            "review_case_id": self.review_case_id,
            "selected_value": self.selected_value,
            "target_fingerprint": self.target_fingerprint,
        }
        expected_request = _sha256(_canonical(request))
        expected_id = "human_decision_" + _sha256(
            _canonical(
                {"request_digest": expected_request, "occurred_at": self.occurred_at}
            )
        )
        if self.request_digest != expected_request or self.decision_id != expected_id:
            raise HumanDecisionError("invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "actor": self.actor,
            "decision_id": self.decision_id,
            "destination": self.destination,
            "idempotency_digest": self.idempotency_digest,
            "occurred_at": self.occurred_at,
            "outcome": self.outcome,
            "proposal_id": self.proposal_id,
            "request_digest": self.request_digest,
            "review_case_id": self.review_case_id,
            "schema_version": "human-decision-v1",
            "selected_value": self.selected_value,
            "target_fingerprint": self.target_fingerprint,
        }

    def to_json(self) -> str:
        return _canonical(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        try:
            raw = json.loads(serialized)
            if type(raw) is not dict or set(raw) != {
                "actor",
                "decision_id",
                "destination",
                "idempotency_digest",
                "occurred_at",
                "outcome",
                "proposal_id",
                "request_digest",
                "review_case_id",
                "schema_version",
                "selected_value",
                "target_fingerprint",
            } or raw["schema_version"] != "human-decision-v1":
                raise ValueError
            value = cls(
                raw["decision_id"],
                raw["review_case_id"],
                raw["proposal_id"],
                raw["target_fingerprint"],
                raw["destination"],
                raw["actor"],
                raw["outcome"],
                raw["selected_value"],
                raw["idempotency_digest"],
                raw["request_digest"],
                raw["occurred_at"],
            )
            request = {
                "actor": value.actor,
                "destination": value.destination,
                "idempotency_digest": value.idempotency_digest,
                "outcome": value.outcome,
                "proposal_id": value.proposal_id,
                "review_case_id": value.review_case_id,
                "selected_value": value.selected_value,
                "target_fingerprint": value.target_fingerprint,
            }
            expected_request = _sha256(_canonical(request))
            expected_id = "human_decision_" + _sha256(
                _canonical(
                    {
                        "request_digest": expected_request,
                        "occurred_at": value.occurred_at,
                    }
                )
            )
            if (
                re.fullmatch(r"human_decision_[0-9a-f]{64}", value.decision_id)
                is None
                or value.decision_id != expected_id
                or value.request_digest != expected_request
                or re.fullmatch(r"review_case_[0-9a-f]{64}", value.review_case_id)
                is None
                or re.fullmatch(r"review_proposal_[0-9a-f]{64}", value.proposal_id)
                is None
                or type(value.destination) is not str
                or not 1 <= len(value.destination) <= 64
                or value.actor != "owner"
                or value.outcome not in _OUTCOMES
                or type(value.occurred_at) is not int
                or value.occurred_at < 0
                or any(
                    type(item) is not str
                    or len(item) != 64
                    or re.fullmatch(r"[0-9a-f]{64}", item) is None
                    for item in (
                        value.idempotency_digest,
                        value.request_digest,
                        value.target_fingerprint,
                    )
                )
                or (value.outcome == "accept") != (type(value.selected_value) is dict)
            ):
                raise ValueError
            return value
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise HumanDecisionError("invalid") from error


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
