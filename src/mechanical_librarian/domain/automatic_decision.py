"""Immutable decision-only facts admitted by an exact qualification envelope."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_QUALIFICATION_CANDIDATE_SHA256,
)


class AutomaticDecisionError(ValueError):
    """Automatic authority is incomplete, stale, or outside its envelope."""


F19A_QUALIFICATION_MANIFEST_SHA256 = "e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec"
_PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS = frozenset({
    "title_layout:preserve", "title_layout:title_and_subtitle",
    "date_precision:year", "date_precision:full_date",
    "language_rendering:preserve", "language_rendering:display_name",
    "contributor_names:preserve", "contributor_names:canonical",
    "publisher_preference:publisher",
})
F19A_QUALIFIED_POLICY_OPTIONS = _PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS | frozenset({
    "title:replace_target:provider_consensus_noise",
})
F38_QUALIFIED_POLICY_OPTIONS = frozenset({
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY}:"
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION}",
})
QUALIFIED_POLICY_OPTIONS = (
    F19A_QUALIFIED_POLICY_OPTIONS
    | F20_PASSING_AUTOMATIC_OPTIONS
    | F38_QUALIFIED_POLICY_OPTIONS
)
_PRIOR_F19A_QUALIFICATION_MANIFESTS = (
    (
        "6708826d4891a228d9d2f664ea134ffad693974144c462370db851e50b330b56",
        _PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37",
        _PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS,
    ),
)
_PRIOR_F20_QUALIFICATION_MANIFESTS = (
    (
        "15cfb36db142e3783fa47a73157c72ecfd08bb282a967abc83fba152d1199205",
        F20_PASSING_AUTOMATIC_OPTIONS,
    ),
    (
        "42a057407a736bfd86f115ae03879f4909fe1217f34ec552c170db1c74af7a8a",
        F20_PASSING_AUTOMATIC_OPTIONS,
    ),
    (
        "9c1410dcc6d221478a2b96afdc51da19fd8a54f82d7023034a3063afdedefc37",
        F20_PASSING_AUTOMATIC_OPTIONS,
    ),
    (
        "f5975691720b81452f2f2b3a41046329c4c600ade45458478aebaf7190392176",
        F20_PASSING_AUTOMATIC_OPTIONS,
    ),
    (
        "6c6648598de1fcc175cbbec0f68908eb6f6ca67b2927e4ef05967551d6a9c542",
        F20_PASSING_AUTOMATIC_OPTIONS,
    ),
    (
        "99ba8426ee53e4d31e52ecf0f65a0fce4eb237d3e5acef42e4ee17217b57dae9",
        frozenset({
            "subtitle:fill_empty",
            "series:primary_narrative", "series:source_order", "series:all_qualified",
            "genres:fill_empty", "genres:add", "genres:replace",
            "tags:fill_empty", "tags:add", "tags:replace",
            "description_fill:safe_plain_text",
            "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
        }),
    ),
    (
        "770b56be2ce7ea9d1d809be189724e5f281a0dd8570af80d63429a5ab617ff99",
        F20_PASSING_AUTOMATIC_OPTIONS,
    ),
    (
        "901e25432750d3f02df1cd311f5a7146e56d5686bd4b6bc0ac589e5db3eb221a",
        frozenset({
            "series:primary_narrative", "series:source_order", "series:all_qualified",
            "genres:add", "genres:replace",
            "tags:fill_empty", "tags:add", "tags:replace",
            "description_fill:safe_plain_text",
            "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
        }),
    ),
    (
        "ba42a24185273170f024d861c5dc6865baf298a785068800ab0a0f3f4a6da31a",
        frozenset({
            "series:primary_narrative", "series:source_order", "series:all_qualified",
            "genres:fill_empty", "genres:add", "genres:replace",
            "tags:fill_empty", "tags:add", "tags:replace",
            "description_fill:safe_plain_text",
            "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
        }),
    ),
    (
        "2628f9f02706e3e0e6afe244231e3fcab387b866007e016b8003f0978bd0780d",
        frozenset({
            "series:primary_narrative", "series:source_order", "series:all_qualified",
            "genres:fill_empty", "genres:add", "genres:replace",
            "tags:fill_empty", "tags:add", "tags:replace",
            "description_fill:safe_plain_text",
            "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
        }),
    ),
)


def automatic_safety_category(policy_category: str, policy_option: str) -> str:
    """Return the suspension lane for one immutable policy identity."""
    if (policy_category, policy_option) == (
        "title",
        "replace_target:provider_consensus_noise",
    ):
        return "title_layout"
    return policy_category


def _current_qualification_manifest_sha256(
    policy_category: str, policy_option: str
) -> str:
    identity = f"{policy_category}:{policy_option}"
    if identity in F19A_QUALIFIED_POLICY_OPTIONS:
        return F19A_QUALIFICATION_MANIFEST_SHA256
    if identity in F38_QUALIFIED_POLICY_OPTIONS:
        return F38_QUALIFICATION_MANIFEST_SHA256
    if identity in F20_PASSING_AUTOMATIC_OPTIONS:
        return F20_QUALIFICATION_CANDIDATE_SHA256
    raise AutomaticDecisionError("invalid")


def qualification_manifest_sha256(policy_category: str, policy_option: str) -> str:
    """Return the current manifest used to create a new decision."""
    return _current_qualification_manifest_sha256(policy_category, policy_option)


def qualification_manifest_is_current(
    policy_category: str,
    policy_option: str,
    manifest_sha256: object,
) -> bool:
    """Return whether one persisted decision still carries effect authority."""
    try:
        return type(manifest_sha256) is str and manifest_sha256 == (
            _current_qualification_manifest_sha256(policy_category, policy_option)
        )
    except AutomaticDecisionError:
        return False


def _qualification_manifest_is_valid(
    policy_category: str, policy_option: str, manifest_sha256: str
) -> bool:
    identity = f"{policy_category}:{policy_option}"
    return manifest_sha256 == _current_qualification_manifest_sha256(
        policy_category, policy_option
    ) or any(
        manifest_sha256 == prior_digest and identity in qualified_options
        for prior_digest, qualified_options in _PRIOR_F20_QUALIFICATION_MANIFESTS
        + _PRIOR_F19A_QUALIFICATION_MANIFESTS
    )


@dataclass(frozen=True, slots=True)
class AutomaticSafetyState:
    """Closed persisted safety posture; suspension never clears by itself."""

    paused: bool
    global_suspension: str | None
    category_suspensions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.paused) is not bool or (
            self.global_suspension is not None
            and (type(self.global_suspension) is not str or not self.global_suspension)
        ) or self.category_suspensions != tuple(sorted(set(self.category_suspensions))) or any(
            type(category) is not str or not category or type(reason) is not str or not reason
            for category, reason in self.category_suspensions
        ):
            raise AutomaticDecisionError("invalid")

    def permits(self, category: str) -> bool:
        return not self.paused and self.global_suspension is None and category not in dict(self.category_suspensions)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AutomaticDecision:
    """A server-authoritative candidate/keep-current or Ignore disposition.

    This is deliberately not a HumanDecision: it has no actor and every input
    that supplied authority is retained in its immutable identity.
    """

    decision_id: str
    review_case_id: str
    report_id: str
    proposal_set_id: str
    proposal_id: str
    target_fingerprint: str
    destination: str
    outcome: str
    candidate_id: str | None
    resolved_policy_id: str
    policy_generation: int
    rule_id: str
    policy_category: str
    policy_option: str
    qualification_manifest_sha256: str
    source_outcome_id: str
    bundle_id: str | None
    idempotency_digest: str
    occurred_at: int

    @classmethod
    def create(
        cls, *, review_case_id: str, report_id: str, proposal_set_id: str,
        proposal_id: str, target_fingerprint: str, destination: str,
        outcome: str, candidate_id: str | None, resolved_policy_id: str,
        policy_generation: int, rule_id: str, policy_category: str,
        policy_option: str, qualification_manifest_sha256: str,
        source_outcome_id: str, bundle_id: str | None, occurred_at: int,
    ) -> "AutomaticDecision":
        if qualification_manifest_sha256 != _current_qualification_manifest_sha256(
            policy_category, policy_option
        ):
            raise AutomaticDecisionError("invalid")
        core = {"candidate_id": candidate_id, "destination": destination,
            "outcome": outcome, "policy_category": policy_category,
            "policy_generation": policy_generation, "policy_option": policy_option,
            "proposal_id": proposal_id, "proposal_set_id": proposal_set_id,
            "qualification_manifest_sha256": qualification_manifest_sha256,
            "report_id": report_id, "resolved_policy_id": resolved_policy_id,
            "review_case_id": review_case_id, "rule_id": rule_id,
            "source_outcome_id": source_outcome_id, "bundle_id": bundle_id,
            "target_fingerprint": target_fingerprint}
        return cls("automatic_decision_" + _digest({**core, "occurred_at": occurred_at}),
            **core, idempotency_digest=_digest(core), occurred_at=occurred_at)

    @classmethod
    def from_json(cls, document: str) -> "AutomaticDecision":
        """Rehydrate one exact canonical persisted automatic decision."""
        try:
            if type(document) is not str:
                raise AutomaticDecisionError("invalid")
            data = json.loads(document)
            expected = {
                "bundle_id", "candidate_id", "decision_id", "destination",
                "idempotency_digest", "occurred_at", "origin",
                "outcome", "policy_category", "policy_generation", "policy_option",
                "proposal_id", "proposal_set_id",
                "qualification_manifest_sha256", "report_id",
                "resolved_policy_id", "review_case_id", "rule_id",
                "schema_version", "source_outcome_id", "target_fingerprint",
            }
            if (
                type(data) is not dict
                or set(data) != expected
                or data.get("origin") != "automatic_policy"
                or data.get("schema_version") != "automatic-decision-v1"
                or _canonical(data) != document
            ):
                raise AutomaticDecisionError("invalid")
            values = dict(data)
            values.pop("origin")
            values.pop("schema_version")
            return cls(**values)
        except AutomaticDecisionError:
            raise
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            raise AutomaticDecisionError("invalid") from None

    def __post_init__(self) -> None:
        valid_id = lambda prefix, value: type(value) is str and re.fullmatch(prefix + r"_[0-9a-f]{64}", value) is not None
        if (
            not valid_id("automatic_decision", self.decision_id)
            or not valid_id("review_case", self.review_case_id)
            or not valid_id("report", self.report_id)
            or not valid_id("proposal_set", self.proposal_set_id)
            or not valid_id("review_proposal", self.proposal_id)
            or not valid_id("shadow_outcome", self.source_outcome_id)
            or not valid_id("shadow_rule", self.rule_id)
            or self.outcome not in {"candidate", "keep_current", "ignore"}
            or (self.outcome == "candidate") != (type(self.candidate_id) is str and re.fullmatch(r"alternative_[0-9a-f]{64}", self.candidate_id) is not None)
            or (self.outcome != "candidate" and self.candidate_id is not None)
            or type(self.policy_generation) is not int or self.policy_generation < 1
            or type(self.occurred_at) is not int or self.occurred_at < 0
            or any(type(v) is not str or not v for v in (self.destination, self.policy_category, self.policy_option))
            or any(re.fullmatch(r"[0-9a-f]{64}", v) is None for v in (self.target_fingerprint, self.qualification_manifest_sha256, self.idempotency_digest))
            or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", self.resolved_policy_id) is None
            or f"{self.policy_category}:{self.policy_option}" not in QUALIFIED_POLICY_OPTIONS
            or not _qualification_manifest_is_valid(
                self.policy_category,
                self.policy_option,
                self.qualification_manifest_sha256,
            )
            or (self.policy_category in {"title_layout", "date_precision"}
                and (type(self.bundle_id) is not str or re.fullmatch(r"automatic_bundle_[0-9a-f]{64}", self.bundle_id) is None))
            or (self.policy_category not in {"title_layout", "date_precision"} and self.bundle_id is not None)
        ):
            raise AutomaticDecisionError("invalid")
        if self.decision_id != "automatic_decision_" + _digest({**self._core(), "occurred_at": self.occurred_at}) or self.idempotency_digest != _digest(self._core()):
            raise AutomaticDecisionError("invalid")

    def _core(self) -> dict[str, object]:
        return {"bundle_id": self.bundle_id, "candidate_id": self.candidate_id, "destination": self.destination, "outcome": self.outcome, "policy_category": self.policy_category, "policy_generation": self.policy_generation, "policy_option": self.policy_option, "proposal_id": self.proposal_id, "proposal_set_id": self.proposal_set_id, "qualification_manifest_sha256": self.qualification_manifest_sha256, "report_id": self.report_id, "resolved_policy_id": self.resolved_policy_id, "review_case_id": self.review_case_id, "rule_id": self.rule_id, "source_outcome_id": self.source_outcome_id, "target_fingerprint": self.target_fingerprint}

    def to_data(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, **self._core(), "idempotency_digest": self.idempotency_digest, "occurred_at": self.occurred_at, "origin": "automatic_policy", "schema_version": "automatic-decision-v1"}

    def to_json(self) -> str:
        return _canonical(self.to_data())
