from __future__ import annotations

import hashlib
import json

import pytest

from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    QUALIFIED_POLICY_OPTIONS,
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
    automatic_safety_category,
    qualification_manifest_is_current,
)
from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_QUALIFICATION_CANDIDATE_SHA256,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def test_candidate_is_immutable_and_binds_all_authority_inputs() -> None:
    value = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="title",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="title_layout",
        policy_option="preserve",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        occurred_at=1,
        bundle_id="automatic_bundle_" + "4" * 64,
    )
    assert "automatic_policy" == value.to_data()["origin"]
    assert "candidate" == value.outcome
    assert value == AutomaticDecision.from_json(value.to_json())


def test_persisted_decision_requires_exact_canonical_document() -> None:
    value = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="language",
        outcome="keep_current",
        candidate_id=None,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="language_rendering",
        policy_option="preserve",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        occurred_at=1,
        bundle_id=None,
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.from_json(value.to_json() + "\n")
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.from_json(
            value.to_json().replace(
                '"schema_version":"automatic-decision-v1"',
                '"schema_version":"automatic-decision-v2"',
            )
        )


def test_qualified_isbn_decision_binds_the_f20_candidate() -> None:
    value = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="isbn",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="isbn_form",
        policy_option="prefer_isbn13",
        qualification_manifest_sha256=F20_QUALIFICATION_CANDIDATE_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        occurred_at=1,
        bundle_id=None,
    )
    assert F20_QUALIFICATION_CANDIDATE_SHA256 == value.qualification_manifest_sha256
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **{
                key: value
                for key, value in value._core().items()
                if key != "qualification_manifest_sha256"
            },
            qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            occurred_at=1,
        )


def test_f38_contributor_consensus_binds_its_own_manifest() -> None:
    value = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="authors",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category=F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
        policy_option=F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
        qualification_manifest_sha256=F38_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        occurred_at=1,
        bundle_id=None,
    )
    assert F38_QUALIFICATION_MANIFEST_SHA256 == value.qualification_manifest_sha256
    assert qualification_manifest_is_current(
        value.policy_category,
        value.policy_option,
        value.qualification_manifest_sha256,
    )
    historical = AutomaticDecision.create(
        **{
            key: value
            for key, value in value._core().items()
            if key not in {"policy_option", "qualification_manifest_sha256"}
        },
        policy_option="canonical",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        occurred_at=2,
    )
    assert (
        F19A_QUALIFICATION_MANIFEST_SHA256 == historical.qualification_manifest_sha256
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **{
                key: value
                for key, value in value._core().items()
                if key != "qualification_manifest_sha256"
            },
            qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            occurred_at=1,
        )


def test_prior_f20_decision_remains_readable_without_granting_new_authority() -> None:
    current = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="genres",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="genres",
        policy_option="add",
        qualification_manifest_sha256=F20_QUALIFICATION_CANDIDATE_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        occurred_at=1,
        bundle_id=None,
    )
    prior_digests = (
        "15cfb36db142e3783fa47a73157c72ecfd08bb282a967abc83fba152d1199205",
        "42a057407a736bfd86f115ae03879f4909fe1217f34ec552c170db1c74af7a8a",
        "9c1410dcc6d221478a2b96afdc51da19fd8a54f82d7023034a3063afdedefc37",
        "f5975691720b81452f2f2b3a41046329c4c600ade45458478aebaf7190392176",
        "6c6648598de1fcc175cbbec0f68908eb6f6ca67b2927e4ef05967551d6a9c542",
        "99ba8426ee53e4d31e52ecf0f65a0fce4eb237d3e5acef42e4ee17217b57dae9",
        "770b56be2ce7ea9d1d809be189724e5f281a0dd8570af80d63429a5ab617ff99",
        "901e25432750d3f02df1cd311f5a7146e56d5686bd4b6bc0ac589e5db3eb221a",
        "ba42a24185273170f024d861c5dc6865baf298a785068800ab0a0f3f4a6da31a",
        "2628f9f02706e3e0e6afe244231e3fcab387b866007e016b8003f0978bd0780d",
        "535348d225d6f160a4096c17c3379ee060741cb1d704ad0d741fc4631fc01d9b",
        "1315e324edd78e698587e14497aa0ab11e33fd65612f00cd8a78a462fd6445fd",
        "c8843d96dfda862b04f6269fd1ab3ab202338815ed5d0efbc0c4c480293adea3",
    )
    for prior_digest in prior_digests:
        prior_core = {
            **current._core(),
            "qualification_manifest_sha256": prior_digest,
        }
        prior = AutomaticDecision(
            decision_id="automatic_decision_"
            + _digest({**prior_core, "occurred_at": 1}),
            **prior_core,
            idempotency_digest=_digest(prior_core),
            occurred_at=1,
        )
        assert prior == AutomaticDecision.from_json(prior.to_json())
        assert not qualification_manifest_is_current(
            prior.policy_category,
            prior.policy_option,
            prior.qualification_manifest_sha256,
        )
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **{
                    key: value
                    for key, value in current._core().items()
                    if key != "qualification_manifest_sha256"
                },
                qualification_manifest_sha256=prior_digest,
                occurred_at=2,
            )

    fill_core = {
        **current._core(),
        "policy_option": "fill_empty",
        "qualification_manifest_sha256": prior_digests[0],
    }
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision(
            decision_id="automatic_decision_" + "4" * 64,
            **fill_core,
            idempotency_digest="5" * 64,
            occurred_at=1,
        )


def test_prior_f19a_language_decision_remains_readable_only() -> None:
    current = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="language",
        outcome="keep_current",
        candidate_id=None,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="language_rendering",
        policy_option="preserve",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        occurred_at=1,
        bundle_id=None,
    )
    prior_digests = (
        "6708826d4891a228d9d2f664ea134ffad693974144c462370db851e50b330b56",
        "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37",
        "e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec",
    )
    for prior_digest in prior_digests:
        prior_core = {
            **current._core(),
            "qualification_manifest_sha256": prior_digest,
        }
        prior = AutomaticDecision(
            decision_id="automatic_decision_"
            + _digest({**prior_core, "occurred_at": 1}),
            **prior_core,
            idempotency_digest=_digest(prior_core),
            occurred_at=1,
        )

        assert prior == AutomaticDecision.from_json(prior.to_json())
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **{
                    key: value
                    for key, value in current._core().items()
                    if key != "qualification_manifest_sha256"
                },
                qualification_manifest_sha256=prior_digest,
                occurred_at=2,
            )


def _f19a_title_decision_document(prior_digest: str, policy_option: str) -> str:
    core = {
        "bundle_id": None,
        "candidate_id": "alternative_" + "f" * 64,
        "destination": "title",
        "outcome": "candidate",
        "policy_category": "title",
        "policy_generation": 1,
        "policy_option": policy_option,
        "proposal_id": "review_proposal_" + "d" * 64,
        "proposal_set_id": "proposal_set_" + "c" * 64,
        "qualification_manifest_sha256": prior_digest,
        "report_id": "report_" + "b" * 64,
        "resolved_policy_id": "resolved_policy_" + "0" * 64,
        "review_case_id": "review_case_" + "a" * 64,
        "rule_id": "shadow_rule_" + "1" * 64,
        "source_outcome_id": "shadow_outcome_" + "3" * 64,
        "target_fingerprint": "e" * 64,
    }
    document = {
        "decision_id": "automatic_decision_" + _digest({**core, "occurred_at": 1}),
        **core,
        "idempotency_digest": _digest(core),
        "occurred_at": 1,
        "origin": "automatic_policy",
        "schema_version": "automatic-decision-v1",
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def test_prior_f19a_manifests_cannot_rehydrate_an_f41_decision() -> None:
    for prior_digest in (
        "6708826d4891a228d9d2f664ea134ffad693974144c462370db851e50b330b56",
        "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37",
        "e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec",
    ):
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.from_json(
                _f19a_title_decision_document(
                    prior_digest, "replace_target:decoration_tidy"
                )
            )


def test_the_f35_manifest_still_rehydrates_its_own_title_decision() -> None:
    document = _f19a_title_decision_document(
        "e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec",
        "replace_target:provider_consensus_noise",
    )

    assert (
        "replace_target:provider_consensus_noise"
        == AutomaticDecision.from_json(document).policy_option
    )


def test_prior_f19a_manifests_cannot_rehydrate_an_f35_decision() -> None:
    for prior_digest in (
        "6708826d4891a228d9d2f664ea134ffad693974144c462370db851e50b330b56",
        "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37",
    ):
        core = {
            "bundle_id": None,
            "candidate_id": "alternative_" + "f" * 64,
            "destination": "title",
            "outcome": "candidate",
            "policy_category": "title",
            "policy_generation": 1,
            "policy_option": "replace_target:provider_consensus_noise",
            "proposal_id": "review_proposal_" + "d" * 64,
            "proposal_set_id": "proposal_set_" + "c" * 64,
            "qualification_manifest_sha256": prior_digest,
            "report_id": "report_" + "b" * 64,
            "resolved_policy_id": "resolved_policy_" + "0" * 64,
            "review_case_id": "review_case_" + "a" * 64,
            "rule_id": "shadow_rule_" + "1" * 64,
            "source_outcome_id": "shadow_outcome_" + "3" * 64,
            "target_fingerprint": "e" * 64,
        }
        document = {
            "decision_id": "automatic_decision_" + _digest({**core, "occurred_at": 1}),
            **core,
            "idempotency_digest": _digest(core),
            "occurred_at": 1,
            "origin": "automatic_policy",
            "schema_version": "automatic-decision-v1",
        }
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.from_json(
                json.dumps(document, separators=(",", ":"), sort_keys=True)
            )


def test_ignore_has_no_candidate() -> None:
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            review_case_id="review_case_" + "a" * 64,
            report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64,
            proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64,
            destination="title",
            outcome="ignore",
            candidate_id="alternative_" + "f" * 64,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1,
            rule_id="shadow_rule_" + "1" * 64,
            policy_category="title_layout",
            policy_option="preserve",
            qualification_manifest_sha256="2" * 64,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            occurred_at=1,
            bundle_id="automatic_bundle_" + "4" * 64,
        )


def test_unqualified_imprint_cannot_create_authority() -> None:
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            review_case_id="review_case_" + "a" * 64,
            report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64,
            proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64,
            destination="publisher",
            outcome="keep_current",
            candidate_id=None,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1,
            rule_id="shadow_rule_" + "1" * 64,
            policy_category="publisher_preference",
            policy_option="imprint",
            qualification_manifest_sha256="2" * 64,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            occurred_at=1,
            bundle_id=None,
        )


def test_safety_state_fails_closed_per_category_and_globally() -> None:
    assert (
        automatic_safety_category(
            F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
            F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
        )
        == "contributor_names"
    )
    assert not AutomaticSafetyState(False, None, (("title_layout", "safety"),)).permits(
        "title_layout"
    )
    assert not AutomaticSafetyState(False, "identity", ()).permits("date_precision")
    assert not AutomaticSafetyState(True, None, ()).permits("date_precision")


def test_qualification_manifest_defers_imprint() -> None:
    assert "publisher_preference:imprint" not in QUALIFIED_POLICY_OPTIONS
