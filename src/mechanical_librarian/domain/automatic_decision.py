"""Immutable decision-only facts admitted by an exact qualification envelope."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from mechanical_librarian.domain import audiobook_decision_qualification
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


F19A_QUALIFICATION_MANIFEST_SHA256 = "0384d1ca5bb8d67cdbe5e0a80ca90952a7fc338db46571d13af804d546e27cb1"
_PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS = frozenset({
    "title_layout:preserve", "title_layout:title_and_subtitle",
    "date_precision:year", "date_precision:full_date",
    "language_rendering:preserve", "language_rendering:display_name",
    "contributor_names:preserve", "contributor_names:canonical",
    "publisher_preference:publisher",
})
_PRE_F41_F19A_QUALIFIED_POLICY_OPTIONS = (
    _PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS
    | frozenset({"title:replace_target:provider_consensus_noise"})
)
_PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS = _PRE_F41_F19A_QUALIFIED_POLICY_OPTIONS | frozenset({
    "title:replace_target:decoration_tidy",
})
F19A_QUALIFIED_POLICY_OPTIONS = _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS | frozenset({
    "title:replace_target:isbn_creator_supported",
})
F38_QUALIFIED_POLICY_OPTIONS = frozenset({
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY}:"
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION}",
})
F46_QUALIFIED_POLICY_OPTIONS = (
    audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
)
QUALIFIED_POLICY_OPTIONS = (
    F19A_QUALIFIED_POLICY_OPTIONS
    | F20_PASSING_AUTOMATIC_OPTIONS
    | F38_QUALIFIED_POLICY_OPTIONS
    | F46_QUALIFIED_POLICY_OPTIONS
)
_PRIOR_F19A_QUALIFICATION_MANIFESTS = (
    ("50eaf202ea5d57407714427863634e45abe4be6380616e7d4c0b811e5df033c6", F19A_QUALIFIED_POLICY_OPTIONS),
    ("c2b2170b1e7ce6f9b199afbbdf303e72cf2fd011bcaa0b4d3e09ab1cbd577583", F19A_QUALIFIED_POLICY_OPTIONS),

    ("15766b5b2232b2bb7e64cefa4e529c134b7c3da829f5f801a1b43afb897b4676", F19A_QUALIFIED_POLICY_OPTIONS),
    ("f30e2c102d55c532dff554f7b6ca17c3998bcd965ce55d9ca6e2df052c48b7d9", _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS),
    ("df88fef11c2d9d015c4642688cf5f5f62902f90de9474eb2828417aaeebdb420", F19A_QUALIFIED_POLICY_OPTIONS),
    ("010c06261591a6f4fd959c8c1fe0cc8e1346b3fc58a7923daf4de1ec93049cae", _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS),
    ("f9f172406ddb2a8b5ca282cad3fa25c29148ac7e04100caccc567c6648d6098b", F19A_QUALIFIED_POLICY_OPTIONS),
    ("91292dd98bef221efc6d75ed336bc1031e3917fa75c05dcd38474eef5b29e523", _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS),
    (
        "01c876cd73706231bf6ab3de9371e0c60b755124d19427a807fc9472b65bbdd0",
        _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "15298611ad0b50dc9a8adb924e6e3551a4da0dd641d2600843f3dac260a71dfb",
        _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS,
    ),
    ("77fd7323b01446306577865ebdb4b47f4fb34748021a89ffdca3fa4d51017335", _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS),
    ("62e8db714fe9a35065bb700e6eac58f1611ec3acca5a90e29965fa2e5c5e6014", _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS),
    ("7f0bfa050ec5956f9d21fcb521f5c06f3a9310cbee42fd7e206a4ec2d71be1e6", _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS),
    (
        "f7a4493d94f0188e6974aad1b68f9238d9e7733aa1a2e064e5663e385af0fe6e",
        _PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "6708826d4891a228d9d2f664ea134ffad693974144c462370db851e50b330b56",
        _PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37",
        _PRE_F35_F19A_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec",
        _PRE_F41_F19A_QUALIFIED_POLICY_OPTIONS,
    ),

)
_PRE_V12_F20_QUALIFIED_POLICY_OPTIONS = frozenset({
    "subtitle:fill_empty",
    "series:primary_narrative", "series:source_order", "series:all_qualified",
    "genres:fill_empty", "genres:add", "genres:replace",
    "tags:fill_empty", "tags:add", "tags:replace",
    "description_fill:safe_plain_text",
    "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
})
_PRIOR_F20_QUALIFICATION_MANIFESTS = (
    ("694b9bfffd787c097893702990b942012f61f82cd5448ea1f55e7a25c53200d2", F20_PASSING_AUTOMATIC_OPTIONS),
    ("91ce3c6433fcdcebad2bdb5b6d44a6ac970ad96fe0901f5dae5fc765d54fce62", F20_PASSING_AUTOMATIC_OPTIONS),
    ('3b29fdb2e345676adb984a334aa19db8e26689e1b5927ed31bc51f1f09b1b54a', _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS),
    ("af0e2de1a9e5dfa7753fc23b873cf413fbbc29e23a44f4408c6457fa13321d04", F20_PASSING_AUTOMATIC_OPTIONS),
    ("2728215f5ade910611d6806e58de40c867a05315efe9670192de48298e54a54b", F20_PASSING_AUTOMATIC_OPTIONS),
    ("74285ed19d72c767b0589486c6850a2808e9fc9911b7a08b08c34e6979f3a8b2", F20_PASSING_AUTOMATIC_OPTIONS),
    ("2e76fdb8372867c95b476208ebb0ae2bd175171439559d4f56eec8d29611c6c6", _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS),
    ("cbdffb1fe38afa6eb03c2a3b9be05bd8ffe8930a884710b96cc25d0fa14f6c5c", F20_PASSING_AUTOMATIC_OPTIONS),
    ("679b4edfa4f3d79919dfed756140d852acc215e79e08d5bee9b77e3a41977321", _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS),
    (
        "f69a500950778b6298c2b149326ddd496bdaa501b5e50a593cd61f47558e32bc",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "d065def423f5e3316172fae8b378ae2a4407481d6ccd1a9db94427baade2a9d1",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    ("aacb6e2ebce0490f58b86c8069963445dcb470ffdd70283b5558dde082449103", _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS),
    (
        "1dcf4278d8cbba00df764ea17b13809a42304fece748632c217531296dfcddee",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "6535df8f7c4cebefa62f9c38ab01823a377c0c7048fca20b81aaf250b498b7ff",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "7aeaca9b020022b9ab385a9baf249d1b63dc7ffcfcc4a20735d6f6480b5b68e7",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "12607559ec04da639e36fe0afe8e9e01eefb59c6851555a43175fe3c07323dc5",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "a813cba313a99597d2669a34ce98ed7454ae85064386a0596d7c7f81854999cd",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "47b142cfea8c5d73ed267cb71f85a0edf3ab4b646ad29bba457a1f03b7aa37f2",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "10b10aa37008e6cfeb01745ebe75eed15913ca2e7c325517e110dbaf30bbdb74",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "4420a5d1ea34f2e46e9c854b07c068e827324fbce942b4da8c301b3cd7409168",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "15cfb36db142e3783fa47a73157c72ecfd08bb282a967abc83fba152d1199205",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "42a057407a736bfd86f115ae03879f4909fe1217f34ec552c170db1c74af7a8a",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "9c1410dcc6d221478a2b96afdc51da19fd8a54f82d7023034a3063afdedefc37",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "f5975691720b81452f2f2b3a41046329c4c600ade45458478aebaf7190392176",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "6c6648598de1fcc175cbbec0f68908eb6f6ca67b2927e4ef05967551d6a9c542",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
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
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
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
    (
        "535348d225d6f160a4096c17c3379ee060741cb1d704ad0d741fc4631fc01d9b",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "1315e324edd78e698587e14497aa0ab11e33fd65612f00cd8a78a462fd6445fd",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "8b14e134a146e8f03f9d36ad78506a7f234b0768ef61620ef1d1234a8df7c4a5",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "c8843d96dfda862b04f6269fd1ab3ab202338815ed5d0efbc0c4c480293adea3",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "4ede15a2aa84e8726ea2dff4e4f9f0d04b27a70276ed53c400c2de5f7619d445",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "091cdf7f08f60e9e4e1860ff574a9c04c405c914d498560e9802294b69c53a30",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "70563a47e768fe51cb911511ca67849893e4495266c84bb2e9fc7f6165ed2879",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "4a89dfc24a27bbe2a548a9b0c5ad2793cf2da1a2b0445f9dd1ad2f9323822bc9",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "a7af724b0711177ff04ff1ae56c8a7db42c1166fe97390c29e60fed506440500",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),
    (
        "14061bb1d1df8305d75cc2330ce37a931d01c954f1be3cd03e7587558704fbfc",
        _PRE_V12_F20_QUALIFIED_POLICY_OPTIONS,
    ),

)
# No audiobook record has been published, so no audiobook digest has ever been
# current and none can be read back. Rotations land here exactly as the ebook
# ones above do.
_PRIOR_F46_QUALIFICATION_MANIFESTS: tuple[tuple[str, frozenset[str]], ...] = ()


def automatic_safety_category(policy_category: str, policy_option: str) -> str:
    """Return the suspension lane for one immutable policy identity."""
    if policy_category == "title":
        # All title elections write the same target through the same lane.
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
    # An audiobook identity carries a third segment, so it can never collide
    # with an ebook option above and no prior envelope can speak for it. The
    # envelope is read live, because a tranche is activated by editing that
    # module alone.
    if audiobook_decision_qualification.audiobook_identity(
        policy_category, policy_option
    ) is not None:
        try:
            return audiobook_decision_qualification.qualification_manifest_sha256(
                identity
            )
        except audiobook_decision_qualification.AudiobookQualificationError as error:
            raise AutomaticDecisionError("invalid") from error
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
    return qualification_manifest_is_current(
        policy_category, policy_option, manifest_sha256
    ) or any(
        manifest_sha256 == prior_digest and identity in qualified_options
        for prior_digest, qualified_options in _PRIOR_F20_QUALIFICATION_MANIFESTS
        + _PRIOR_F19A_QUALIFICATION_MANIFESTS
        + _PRIOR_F46_QUALIFICATION_MANIFESTS
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
