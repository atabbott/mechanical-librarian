"""Immutable, exact-version human decisions for ordinary review fields."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Mapping, Self

from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
    proposal_media_kind,
    review_proposals,
)

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_OUTCOMES = {"accept", "keep_current", "reject"}
# What a recorded decision may go on to do. Every decision formed since F47
# carries `apply` on both media; `recorded_only` survives only as the F45
# history of decisions taken before audiobook writes existed (#968).
APPLICATIONS = ("apply", "recorded_only")
RECORDED_ONLY_REASON = (
    "recorded, not applied — decided before audiobook writes were available"
)
HumanDecisionAction = Literal["accept", "keep_current"]


def admitted_applications(media_kind: str) -> frozenset[str]:
    """Say which applications a retained decision on this case may carry.

    One derivation, used wherever a retained document is validated. A
    `recorded_only` document is F45 history that only an audiobook case can
    carry, so the same document is stale against an ebook case; new commands
    are narrower still and must say `apply` (#968).
    """
    return (
        frozenset({"apply", "recorded_only"})
        if media_kind == "audiobook"
        else frozenset({"apply"})
    )


class HumanDecisionError(ValueError):
    """A fixed, value-free decision validation or state failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def decision_actions(proposal: Mapping[str, object]) -> tuple[HumanDecisionAction, ...]:
    """Return exactly the new owner choices the immutable proposal admits."""
    result = proposal.get("result")
    if not _actionable(proposal) or type(result) is not dict:
        return ()
    alternatives = result.get("alternatives")
    if type(alternatives) is not list:
        return ()
    eligible = [
        alternative
        for alternative in alternatives
        if type(alternative) is dict
        and alternative.get("assessment") != "preserves_target"
    ]
    # ``reject`` remains readable historical state. A retain-current choice is
    # meaningful only beside an eligible alternative.
    return ("keep_current", "accept") if eligible else ()


def validate_decision_action(
    proposal: Mapping[str, object],
    outcome: str,
    selected_value: dict[str, object] | None,
) -> None:
    """Apply the selection half of the human-decision contract."""
    # Historical ``reject`` facts remain constructible and valid.  It is not
    # returned by ``decision_actions``, so no current projection can offer it.
    if outcome == "reject":
        if not _actionable(proposal):
            raise HumanDecisionError("unsupported")
        if selected_value is not None:
            raise HumanDecisionError("invalid_selection")
        return
    if outcome not in decision_actions(proposal):
        raise HumanDecisionError("unsupported")
    if outcome == "accept":
        result = proposal["result"]
        assert type(result) is dict
        alternatives = result["alternatives"]
        assert type(alternatives) is list
        if type(selected_value) is not dict or selected_value not in [
            alternative.get("value")
            for alternative in alternatives
            if type(alternative) is dict
            and alternative.get("assessment") != "preserves_target"
        ]:
            raise HumanDecisionError("invalid_selection")
    elif selected_value is not None:
        raise HumanDecisionError("invalid_selection")


def _validate_retained_decision_action(
    proposal: Mapping[str, object],
    outcome: str,
    selected_value: dict[str, object] | None,
) -> None:
    """Validate an immutable historical decision without reopening its command envelope."""
    if outcome != "accept":
        # Retained non-accept facts retain the established command validator's
        # exact rejection identity.  Only historical accepts need the broader
        # all-alternative membership check below.
        validate_decision_action(proposal, outcome, selected_value)
        return
    if outcome == "accept":
        result = proposal.get("result")
        alternatives = result.get("alternatives") if type(result) is dict else None
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


def _actionable(proposal: Mapping[str, object]) -> bool:
    result = proposal.get("result")
    return type(result) is dict and (
        result.get("outcome")
        in {"propose_change", "acceptable_choice", "unresolved_choice"}
        or proposal.get("manual_required") is True
    )


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
    application: str

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
            and self.idempotency_digest == self.idempotency_digest_for(idempotency_key)
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
        validate_decision_action(proposal, outcome, selected_value)
        idempotency_digest = cls.idempotency_digest_for(idempotency_key)
        application = "apply"
        request = {
            "actor": actor,
            "application": application,
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
            application,
        )
        value.validate_for(case)
        return value

    def validate_for(self, case: ReviewCase) -> None:
        """Validate a retained immutable fact against its canonical case."""
        self._validate_for(
            case.review_case_id,
            case.target_fingerprint,
            list(case.proposals),
            applications=admitted_applications(case.media_kind),
        )

    def validate_new_for(self, case: ReviewCase) -> None:
        """Validate a newly submitted command against the current owner envelope."""
        self._validate_for(
            case.review_case_id,
            case.target_fingerprint,
            list(case.proposals),
            new_command=True,
            applications=admitted_applications(case.media_kind),
        )

    def validate_for_browse_case(
        self, review_case_id: str, case: dict[str, object]
    ) -> None:
        """Validate against the bounded case envelope used by browse projections."""
        target_fingerprint = case.get("target_fingerprint")
        proposal_set_id = case.get("proposal_set_id")
        proposal_document = case.get("proposal_document")
        results = case.get("results")
        if (
            type(review_case_id) is not str
            or type(target_fingerprint) is not str
            or type(proposal_set_id) is not str
            or type(proposal_document) is not str
            or type(results) is not list
            or any(type(result) is not dict for result in results)
        ):
            raise HumanDecisionError("stale")
        try:
            proposals = review_proposals(proposal_set_id, proposal_document, results)
            media_kind = proposal_media_kind(proposal_document)
        except ReviewCaseValueError as error:
            raise HumanDecisionError("stale") from error
        self._validate_for(
            review_case_id,
            target_fingerprint,
            proposals,
            applications=admitted_applications(media_kind),
        )

    def _validate_for(
        self,
        review_case_id: str,
        target_fingerprint: str,
        proposals: list[object] | tuple[dict[str, object], ...],
        *,
        new_command: bool = False,
        applications: frozenset[str],
    ) -> None:
        proposal = next(
            (
                value
                for value in proposals
                if type(value) is dict
                if value["destination"] == self.destination
                and value["proposal_id"] == self.proposal_id
            ),
            None,
        )
        if (
            self.review_case_id != review_case_id
            or self.target_fingerprint != target_fingerprint
            or proposal is None
            or self.application not in applications
            or (new_command and self.application != "apply")
        ):
            raise HumanDecisionError("stale")
        validator = (
            validate_decision_action
            if new_command
            else _validate_retained_decision_action
        )
        validator(proposal, self.outcome, self.selected_value)
        request = {
            "actor": self.actor,
            "application": self.application,
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
            "application": self.application,
            "decision_id": self.decision_id,
            "destination": self.destination,
            "idempotency_digest": self.idempotency_digest,
            "occurred_at": self.occurred_at,
            "outcome": self.outcome,
            "proposal_id": self.proposal_id,
            "request_digest": self.request_digest,
            "review_case_id": self.review_case_id,
            "schema_version": "human-decision-v2",
            "selected_value": self.selected_value,
            "target_fingerprint": self.target_fingerprint,
        }

    def to_json(self) -> str:
        return _canonical(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        try:
            raw = json.loads(serialized)
            if (
                type(raw) is not dict
                or set(raw)
                != {
                    "actor",
                    "application",
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
                }
                or raw["schema_version"] != "human-decision-v2"
            ):
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
                raw["application"],
            )
            request = {
                "actor": value.actor,
                "application": value.application,
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
                re.fullmatch(r"human_decision_[0-9a-f]{64}", value.decision_id) is None
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
                or value.application not in APPLICATIONS
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
