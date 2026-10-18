from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mechanical_librarian.domain import (
    audiobook_decision_qualification,
    automatic_decision,
)
from mechanical_librarian.domain.audiobook_decision_qualification import (
    F46_SERIES_IDENTITY,
)
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


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize("narrowed", (False, True))
@pytest.mark.parametrize(
    "prior_digest",
    (
        "4ede15a2aa84e8726ea2dff4e4f9f0d04b27a70276ed53c400c2de5f7619d445",
        "091cdf7f08f60e9e4e1860ff574a9c04c405c914d498560e9802294b69c53a30",
        "4420a5d1ea34f2e46e9c854b07c068e827324fbce942b4da8c301b3cd7409168",
        "28b95c60856b1c6a5f88b6e67f7cba9c55b215c10567eeb3b29d2a7e98233175",
    ),
)
def test_v11_v12_series_history_survives_narrowed_current_authority(
    monkeypatch: pytest.MonkeyPatch, option: str, narrowed: bool, prior_digest: str
) -> None:
    prior_core = {
        "review_case_id": "review_case_" + "a" * 64,
        "report_id": "report_" + "b" * 64,
        "proposal_set_id": "proposal_set_" + "c" * 64,
        "proposal_id": "review_proposal_" + "d" * 64,
        "target_fingerprint": "e" * 64,
        "destination": "series",
        "outcome": "candidate",
        "candidate_id": "alternative_" + "f" * 64,
        "resolved_policy_id": "resolved_policy_" + "0" * 64,
        "policy_generation": 1,
        "rule_id": "shadow_rule_" + "1" * 64,
        "policy_category": "series",
        "policy_option": option,
        "qualification_manifest_sha256": prior_digest,
        "source_outcome_id": "shadow_outcome_" + "3" * 64,
        "bundle_id": None,
    }
    document = json.dumps(
        {
            **prior_core,
            "decision_id": "automatic_decision_"
            + _digest({**prior_core, "occurred_at": 1}),
            "idempotency_digest": _digest(prior_core),
            "occurred_at": 1,
            "origin": "automatic_policy",
            "schema_version": "automatic-decision-v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    # A future qualification may narrow the live options; historical authority
    # belongs to its exact frozen manifest rather than that live option set.
    current = AutomaticDecision.create(
        **{
            **prior_core,
            "qualification_manifest_sha256": F20_QUALIFICATION_CANDIDATE_SHA256,
        },
        occurred_at=2,
    )
    assert qualification_manifest_is_current(
        "series", option, current.qualification_manifest_sha256
    )
    if narrowed:
        monkeypatch.setattr(
            automatic_decision, "F20_PASSING_AUTOMATIC_OPTIONS", frozenset()
        )
        monkeypatch.setattr(automatic_decision, "QUALIFIED_POLICY_OPTIONS", frozenset())
    prior = AutomaticDecision.from_json(document)
    assert prior.to_json() == document
    assert not qualification_manifest_is_current(
        "series", option, prior.qualification_manifest_sha256
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(**prior._core(), occurred_at=2)
    for invalid in (
        {"qualification_manifest_sha256": "9" * 64},
        {"policy_option": "invented_membership"},
    ):
        invalid_core = {**prior._core(), **invalid}
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision(
                **invalid_core,
                decision_id="automatic_decision_"
                + _digest({**invalid_core, "occurred_at": 1}),
                idempotency_digest=_digest(invalid_core),
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
        "f69a500950778b6298c2b149326ddd496bdaa501b5e50a593cd61f47558e32bc",
        "d065def423f5e3316172fae8b378ae2a4407481d6ccd1a9db94427baade2a9d1",
        "aacb6e2ebce0490f58b86c8069963445dcb470ffdd70283b5558dde082449103",
        "1dcf4278d8cbba00df764ea17b13809a42304fece748632c217531296dfcddee",
        "6535df8f7c4cebefa62f9c38ab01823a377c0c7048fca20b81aaf250b498b7ff",
        "7aeaca9b020022b9ab385a9baf249d1b63dc7ffcfcc4a20735d6f6480b5b68e7",
        "12607559ec04da639e36fe0afe8e9e01eefb59c6851555a43175fe3c07323dc5",
        "a813cba313a99597d2669a34ce98ed7454ae85064386a0596d7c7f81854999cd",
        "47b142cfea8c5d73ed267cb71f85a0edf3ab4b646ad29bba457a1f03b7aa37f2",
        "10b10aa37008e6cfeb01745ebe75eed15913ca2e7c325517e110dbaf30bbdb74",
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
        "70563a47e768fe51cb911511ca67849893e4495266c84bb2e9fc7f6165ed2879",
        "4a89dfc24a27bbe2a548a9b0c5ad2793cf2da1a2b0445f9dd1ad2f9323822bc9",
        "a7af724b0711177ff04ff1ae56c8a7db42c1166fe97390c29e60fed506440500",
        "679b4edfa4f3d79919dfed756140d852acc215e79e08d5bee9b77e3a41977321",
        "2e76fdb8372867c95b476208ebb0ae2bd175171439559d4f56eec8d29611c6c6",
        "3b29fdb2e345676adb984a334aa19db8e26689e1b5927ed31bc51f1f09b1b54a",
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
        "01c876cd73706231bf6ab3de9371e0c60b755124d19427a807fc9472b65bbdd0",
        "15298611ad0b50dc9a8adb924e6e3551a4da0dd641d2600843f3dac260a71dfb",
        "7f0bfa050ec5956f9d21fcb521f5c06f3a9310cbee42fd7e206a4ec2d71be1e6",
        "f7a4493d94f0188e6974aad1b68f9238d9e7733aa1a2e064e5663e385af0fe6e",
        "6708826d4891a228d9d2f664ea134ffad693974144c462370db851e50b330b56",
        "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37",
        "e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec",
        "91292dd98bef221efc6d75ed336bc1031e3917fa75c05dcd38474eef5b29e523",
        "010c06261591a6f4fd959c8c1fe0cc8e1346b3fc58a7923daf4de1ec93049cae",
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


def test_only_current_manifest_can_admit_isbn_title_option() -> None:
    option = "replace_target:isbn_creator_supported"
    current = AutomaticDecision.from_json(
        _f19a_title_decision_document(F19A_QUALIFICATION_MANIFEST_SHA256, option)
    )
    assert qualification_manifest_is_current(
        "title", option, current.qualification_manifest_sha256
    )
    for (
        prior_digest,
        options,
    ) in automatic_decision._PRIOR_F19A_QUALIFICATION_MANIFESTS:
        if "title:" + option in options:
            historical = AutomaticDecision.from_json(
                _f19a_title_decision_document(prior_digest, option)
            )
            assert not qualification_manifest_is_current(
                "title", option, historical.qualification_manifest_sha256
            )
        else:
            with pytest.raises(AutomaticDecisionError):
                AutomaticDecision.from_json(
                    _f19a_title_decision_document(prior_digest, option)
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


@pytest.mark.parametrize(
    "option",
    ["replace_target:provider_consensus_noise", "replace_target:decoration_tidy"],
)
@pytest.mark.parametrize(
    "prior_digest",
    (
        "7f0bfa050ec5956f9d21fcb521f5c06f3a9310cbee42fd7e206a4ec2d71be1e6",
        "62e8db714fe9a35065bb700e6eac58f1611ec3acca5a90e29965fa2e5c5e6014",
    ),
)
def test_prior_f19a_v6_title_remains_readable_without_current_authority(
    option: str,
    prior_digest: str,
) -> None:
    decision = AutomaticDecision.from_json(
        _f19a_title_decision_document(prior_digest, option)
    )
    assert AutomaticDecision.from_json(decision.to_json()) == decision
    assert not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(**decision._core(), occurred_at=2)


@pytest.mark.parametrize(
    "name,digest",
    [
        (
            "exact_title",
            "358e921582a2a8799fb2c9ff0aca7825b7fcda81c62ac52371c7970d7a445998",
        ),
        (
            "description_preserved",
            "51e3e24c44821628071260cc676a1a37a8293fcec69d6de73d30402bd21a03e3",
        ),
    ],
)
def test_captured_base_producers_remain_history_without_new_effect_authority(
    name: str, digest: str
) -> None:
    import hashlib
    from pathlib import Path

    document = (
        Path(__file__).parents[1]
        / "support/automatic_decision_predecessors"
        / f"{name}-before-f39.json"
    ).read_bytes()
    assert hashlib.sha256(document).hexdigest() == digest
    decision = AutomaticDecision.from_json(document.decode())
    assert AutomaticDecision.from_json(decision.to_json()) == decision
    assert not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(**decision._core(), occurred_at=2)


def test_both_v22_histories_retain_the_same_thirteen_noncurrent_options() -> None:
    from mechanical_librarian.domain.automatic_decision import (
        _PRIOR_F20_QUALIFICATION_MANIFESTS,
    )

    old = "1dcf4278d8cbba00df764ea17b13809a42304fece748632c217531296dfcddee"
    delivered = "aacb6e2ebce0490f58b86c8069963445dcb470ffdd70283b5558dde082449103"
    histories = dict(_PRIOR_F20_QUALIFICATION_MANIFESTS)
    assert len(histories) == len(_PRIOR_F20_QUALIFICATION_MANIFESTS)
    assert histories[old] == histories[delivered]
    assert len(histories[old]) == 13
    assert F20_QUALIFICATION_CANDIDATE_SHA256 not in {old, delivered}


def _f46_record(monkeypatch: pytest.MonkeyPatch, identity: str, digest: str) -> None:
    """Publish one test-only F46 record for exactly one tranche."""
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        frozenset({identity}),
    )
    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_QUALIFICATION_MANIFEST_SHA256", digest
    )


def test_an_audiobook_identity_binds_the_f46_manifest_and_nothing_prior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recording's authority comes from its own record or from nowhere.

    Until an F46 record exists the identity mints nothing at all, whatever
    digest is offered. Once one exists it binds that digest and only that one:
    no ebook envelope, current or historical, can rehydrate a recording's
    decision, and the record cannot speak for the ebook option it was named
    after (#967).
    """
    identity = F46_SERIES_IDENTITY
    category, option = identity.split(":", 1)
    record = "7" * 64
    core = {
        "review_case_id": "review_case_" + "a" * 64,
        "report_id": "report_" + "b" * 64,
        "proposal_set_id": "proposal_set_" + "c" * 64,
        "proposal_id": "review_proposal_" + "d" * 64,
        "target_fingerprint": "e" * 64,
        "destination": "series",
        "outcome": "candidate",
        "candidate_id": "alternative_" + "f" * 64,
        "resolved_policy_id": "resolved_policy_" + "0" * 64,
        "policy_generation": 1,
        "rule_id": "shadow_rule_" + "1" * 64,
        "policy_category": category,
        "policy_option": option,
        "source_outcome_id": "shadow_outcome_" + "3" * 64,
        "bundle_id": None,
    }

    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_PASSING_AUTOMATIC_OPTIONS", frozenset()
    )
    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_QUALIFICATION_MANIFEST_SHA256", None
    )
    for offered in (
        record,
        F19A_QUALIFICATION_MANIFEST_SHA256,
        F20_QUALIFICATION_CANDIDATE_SHA256,
        F38_QUALIFICATION_MANIFEST_SHA256,
    ):
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **core, qualification_manifest_sha256=offered, occurred_at=1
            )
        assert not qualification_manifest_is_current(category, option, offered)

    _f46_record(monkeypatch, identity, record)
    value = AutomaticDecision.create(
        **core, qualification_manifest_sha256=record, occurred_at=1
    )
    assert record == value.qualification_manifest_sha256
    assert qualification_manifest_is_current(category, option, record)
    assert value == AutomaticDecision.from_json(value.to_json())
    # The identity is its own lane, so a suspension of the base category is
    # what withholds it.
    assert "series" == automatic_safety_category(category, option)

    for offered in (
        F19A_QUALIFICATION_MANIFEST_SHA256,
        F20_QUALIFICATION_CANDIDATE_SHA256,
        F38_QUALIFICATION_MANIFEST_SHA256,
        "12607559ec04da639e36fe0afe8e9e01eefb59c6851555a43175fe3c07323dc5",
    ):
        assert not qualification_manifest_is_current(category, option, offered)
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **core, qualification_manifest_sha256=offered, occurred_at=2
            )
    # The record was named after an ebook option; it never speaks for one.
    assert not qualification_manifest_is_current("series", "primary_narrative", record)
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **{**core, "policy_option": "primary_narrative"},
            qualification_manifest_sha256=record,
            occurred_at=3,
        )
    # Rotating the record leaves the persisted decision readable history and
    # strips its effect authority, exactly as an ebook rotation does.
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_QUALIFICATION_MANIFEST_SHA256",
        "8" * 64,
    )
    monkeypatch.setattr(
        automatic_decision,
        "_PRIOR_F46_QUALIFICATION_MANIFESTS",
        ((record, frozenset({identity})),),
    )
    assert value == AutomaticDecision.from_json(value.to_json())
    assert not qualification_manifest_is_current(category, option, record)
    assert qualification_manifest_is_current(category, option, "8" * 64)
    # Withdrawing it entirely refuses even the read: nothing in this envelope
    # ever spoke for that identity.
    monkeypatch.setattr(automatic_decision, "_PRIOR_F46_QUALIFICATION_MANIFESTS", ())
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        frozenset(),
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.from_json(value.to_json())


def test_an_unqualified_audiobook_tranche_never_mints_a_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every named tranche fails closed while its own record is absent (#967)."""
    _f46_record(monkeypatch, F46_SERIES_IDENTITY, "7" * 64)
    series_category, series_option = F46_SERIES_IDENTITY.split(":", 1)
    assert qualification_manifest_is_current(series_category, series_option, "7" * 64)
    for identity in sorted(audiobook_decision_qualification.F46_AUDIOBOOK_IDENTITIES):
        if identity == F46_SERIES_IDENTITY:
            continue
        category, option = identity.split(":", 1)
        assert not qualification_manifest_is_current(category, option, "7" * 64)
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                review_case_id="review_case_" + "a" * 64,
                report_id="report_" + "b" * 64,
                proposal_set_id="proposal_set_" + "c" * 64,
                proposal_id="review_proposal_" + "d" * 64,
                target_fingerprint="e" * 64,
                destination=identity.split(":", 1)[0],
                outcome="candidate",
                candidate_id="alternative_" + "f" * 64,
                resolved_policy_id="resolved_policy_" + "0" * 64,
                policy_generation=1,
                rule_id="shadow_rule_" + "1" * 64,
                policy_category=category,
                policy_option=option,
                qualification_manifest_sha256="7" * 64,
                source_outcome_id="shadow_outcome_" + "3" * 64,
                bundle_id=None,
                occurred_at=1,
            )


def test_each_historical_manifest_digest_has_one_frozen_option_binding() -> None:
    for rows in (
        automatic_decision._PRIOR_F19A_QUALIFICATION_MANIFESTS,
        automatic_decision._PRIOR_F20_QUALIFICATION_MANIFESTS,
        automatic_decision._PRIOR_F46_QUALIFICATION_MANIFESTS,
    ):
        assert len(rows) == len({digest for digest, _ in rows})
    f19a_bindings = dict(automatic_decision._PRIOR_F19A_QUALIFICATION_MANIFESTS)
    for digest in (
        "91292dd98bef221efc6d75ed336bc1031e3917fa75c05dcd38474eef5b29e523",
        "010c06261591a6f4fd959c8c1fe0cc8e1346b3fc58a7923daf4de1ec93049cae",
    ):
        assert (
            f19a_bindings[digest]
            == automatic_decision._PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS
        )


@pytest.mark.parametrize(
    "option,kind",
    [
        ("preserve", "ordinary_title_preservation_v1"),
        ("title_and_subtitle", "ordinary_title_preservation_v1"),
        ("preserve:audiobook_title_preserve", "audiobook_title_preservation_v1"),
        (
            "title_and_subtitle:audiobook_title_preserve",
            "audiobook_title_preservation_v1",
        ),
    ],
)
def test_title_preservation_witness_is_closed_and_retains_audiobook_identity(
    option, kind
):
    from types import SimpleNamespace

    from mechanical_librarian.domain.automatic_decision import title_preservation_bundle

    values = dict(
        destination="title",
        policy_category="title_layout",
        policy_option=option,
        disposition="keep_current",
        candidate_id=None,
        combination_ids=(),
        citation_ids=("citation-one", "citation-two"),
        reason_code="unique_supported_preservation",
        outcome_id="bound-outcome",
    )
    assert title_preservation_bundle(
        SimpleNamespace(**values)
    ) == "automatic_bundle_" + _digest(
        {
            "kind": kind,
            "source_outcome_id": "bound-outcome",
            "citation_ids": ["citation-one", "citation-two"],
        }
    )
    for change in (
        {"destination": "subtitle"},
        {"policy_category": "title"},
        {"policy_option": "other"},
        {"disposition": "candidate"},
        {"candidate_id": "alternative"},
        {"combination_ids": ("combination",)},
        {"citation_ids": ()},
        {"reason_code": "target_only_preservation"},
    ):
        assert title_preservation_bundle(SimpleNamespace(**(values | change))) is None


@pytest.mark.parametrize(
    "prior_digest,original_options",
    automatic_decision._PRIOR_F46_QUALIFICATION_MANIFESTS,
)
def test_historical_f46_documents_preserve_frozen_synopsis_membership(
    prior_digest, original_options
):
    from pathlib import Path

    pre_v19 = frozenset(
        json.loads(
            Path(
                "tests/fixtures/qualification-authority-predecessor/f46-v18-operations.json"
            ).read_text()
        )
    )
    added = {
        "description_fill:safe_plain_text:audiobook_description_preserve",
        "description_fill:replace_safe_plain_text:audiobook_description_preserve",
        "description_fill:replace_safe_plain_text:audiobook_description_fill",
    }
    assert len(pre_v19) == 73
    assert (
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
        == pre_v19 | added
    )
    if original_options & added:
        assert original_options == pre_v19 | added
    else:
        assert original_options <= pre_v19
    for identity in sorted(original_options | added):
        category, option = identity.split(":", 1)
        destination = next(
            destination
            for destination, identities in audiobook_decision_qualification.F46_IDENTITIES_BY_DESTINATION.items()
            if identity in identities
        )
        arguments = dict(
            review_case_id="review_case_" + "a" * 64,
            report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64,
            proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64,
            destination=destination,
            outcome="keep_current" if identity.endswith("_preserve") else "candidate",
            candidate_id=None
            if identity.endswith("_preserve")
            else "alternative_" + "f" * 64,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1,
            rule_id="shadow_rule_" + "1" * 64,
            policy_category=category,
            policy_option=option,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            bundle_id="automatic_bundle_" + "4" * 64
            if category in {"title_layout", "date_precision"}
            else None,
            qualification_manifest_sha256=prior_digest,
        )
        assert not qualification_manifest_is_current(category, option, prior_digest)
        document = json.dumps(
            {
                **arguments,
                "decision_id": "automatic_decision_"
                + _digest({**arguments, "occurred_at": 1}),
                "idempotency_digest": _digest(arguments),
                "occurred_at": 1,
                "origin": "automatic_policy",
                "schema_version": "automatic-decision-v1",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        if identity not in original_options:
            with pytest.raises(AutomaticDecisionError):
                AutomaticDecision.from_json(document)
            current = AutomaticDecision.create(
                **(
                    arguments
                    | {
                        "qualification_manifest_sha256": audiobook_decision_qualification.F46_QUALIFICATION_MANIFEST_SHA256
                    }
                ),
                occurred_at=1,
            )
            assert AutomaticDecision.from_json(current.to_json()) == current
        else:
            assert AutomaticDecision.from_json(document).to_json() == document


# The source-only re-pins retain v18 membership; three intermediate manifests
# are absent, but their exact aliases resolve to this set in pre-v19 source
# 79dbbf4c3eb58365e266655c32a0f6de3c784e51. Earlier explicit sets stay separate.
@pytest.mark.parametrize(
    "prior_digest",
    [
        "6ee296bc750e68009a24b68b5fbc2202370f0d5a33e9cb47eee4cdec00535ebc",
        "97874e99e016627d077ac503e8ab18c017d4444b06e0ee5a2dfb21e37e36b7d1",
        "44a590388d697fb9d61faaea4e96010d4ccf23ea50577ae9af62e8446e872eb1",
        "88f79b6038765c0a0932e6bd9893ada5f9d390ec2dd224555c9133182766a851",
        "66c8547a5092ec3665aaa566a75599bf22ba7b634d18e6927ae39b813db03489",
        "d49a0313ac599b9db55f6fe7e9eb15bf2dd7df01c4ecb707013b6115b6ac7aaa",
        "ef836f9cdc6b45d208bf05f406ffb3037864abf78a0e7262d471050a7b394b26",
        "16eabc9abc5d141a4ce85bf1f5b28c909c5705b49d7642cec3ed75989f6f8e98",
        "8e43f608421f18bdcca447eb0b99c2b676648f8a156a9e4ac0e57a2e093eec7a",
        "3bc740723d81d15cac137aa92e3731c4cf5da848a03ff88016e36db9553acd63",
        "6dbedf9d8e953928300425aef493508aa74798b6c0ef6a5d6fec756a3bd7a366",
        "e675cf0efcc753ecdcb672bf96e4d0b225ece7f3822fe5d54316fc867b5ed41b",
        "6cf0944a817d176d2224a97e971f059314d109c96921e6e94eb8f073d216fa87",
        "29b89f37d509bcff9f09317f9c76a62af1309b1e71e8d8ebf9cb84a835c65b55",
        "5c99d641474bc81a538463eec6cd619de7d6770a4e3876bef20a1b9ac3bf5656",
        "5e2d5bb1306585f45db4a7d2788791f1c98152b40cff272b2e9d34c5b3b8748d",
        "8a708d1bc25ca8cb42e42a09faa9616f0605eadcb0584f0b8a6f7cdfcf7750bc",
        "e6160457f180870eaf6914bc2b15ff2b18246e721cd6d101e8f2a40a6334c796",
    ],
)
def test_pre_v19_f46_aliases_retain_every_original_operation(prior_digest):
    from pathlib import Path

    expected = frozenset(
        json.loads(
            Path(
                "tests/fixtures/qualification-authority-predecessor/f46-v18-operations.json"
            ).read_text()
        )
    )
    assert (
        dict(automatic_decision._PRIOR_F46_QUALIFICATION_MANIFESTS)[prior_digest]
        == expected
    )


@pytest.mark.parametrize(
    "digest,permissions",
    (
        (
            "f7eabaf07b3893babd2bbf4dd45d299b58a37b1d2f427ad663e31fca548474b7",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
        ),
        (
            "622be5be6e6ac7b8a8506bb0045ef1c0a2ee8aec4958f2550e72e92078a8c805",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
        ),
        (
            "0fb2395d521fa24ff95ee716c64ff8ee6aa40e95085ef938de50d716e221caac",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
        ),
        pytest.param(
            "6151378c5ec4c044731eef559736ca9a09144c271644f7bfd8970c7d2fa98337",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="preparation-predecessor-f19a",
        ),
        pytest.param(
            "fb1c3754ef9167a37fe49e27421688632e8ed8871e539289be5385ce483c8520",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="preparation-predecessor-f20",
        ),
        pytest.param(
            "96a1a33494ebf7af4c4cf2146b124fc75f6f489567f8b2281adce25787f5bc59",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="preparation-predecessor-f46",
        ),
        (
            "796b2a4d55851a997dae96f0863a527c8820a841e1a90c9712f4cf9fc50a4c29",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
        ),
        (
            "19939b85acb471fb17c076a44c6a0dd173f6c32e5a951986b9e65060458cfd8a",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
        ),
        (
            "1380202f048a6f7dbd006041c18ce6eded8d7c1ecd5522f330af6f2023b0a12c",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
        ),
        pytest.param(
            "631286528f23707c48a5782c43c108ad7a721c4a10b0c34199246fa68adde51a",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="asset-cache-predecessor-f19a",
        ),
        pytest.param(
            "5cc75cc1a2a4fe4dc1f4319ddde7a7adec586aba931b053cfcbeadc0550763ee",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="asset-cache-predecessor-f20",
        ),
        pytest.param(
            "9794229c4ba7dcd86a16ba10779910093029f75e36aeb2d3c4b5263053f502f9",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="asset-cache-predecessor-f46",
        ),
        pytest.param(
            "f8dd88e5cf53a6e47871d8f8bf3357331a6b59baa72827b23891944c47d62344",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="browse-facts-predecessor-f19a",
        ),
        pytest.param(
            "688d5d9737f27d7edafda8dcb96ea0a5396f022860e93f9d065239317f78c254",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="browse-facts-predecessor-f20",
        ),
        pytest.param(
            "f93b61f767eafd8a0e5438886171743f84311a2c5a5a964ec0632ad538442e16",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="browse-facts-predecessor-f46",
        ),
        pytest.param(
            "d9f6e90b2bcb0b020556f7492990ee86727482f7148579443d2d1bf9ac49d6c4",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="persisted-browse-facts-predecessor-f19a",
        ),
        pytest.param(
            "f5babe5b7e176e52afa45aa4600647cfe435e1df8bbfad4ccd4e8f80703fd65b",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="persisted-browse-facts-predecessor-f20",
        ),
        pytest.param(
            "e557305e9830036ce0bb313dbe38b92ed0b05fd7c7db9f95f1d30668f633073b",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="persisted-browse-facts-predecessor-f46",
        ),
        pytest.param(
            "d568b1d59a99ab6cd2e50165a7365742ab317fa2d4a0f76de877a2e185e9ea1d",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="request-service-predecessor-f19a",
        ),
        pytest.param(
            "74fe1f1bef806bd0df74b39d4931c1db997a2707d1c814723c9cd127a022bb61",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="request-service-predecessor-f20",
        ),
        pytest.param(
            "37eb06232c5b346d1f1b35386cfac4666f620a5c91852099345d85082b6afc69",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="request-service-predecessor-f46",
        ),
        pytest.param(
            "ba8c43413b9419d41f225c99e81d72b87a3ffb955bff10449dc1fb5676ead0e0",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="field-effect-scan-predecessor-f19a",
        ),
        pytest.param(
            "7c0bc2cb436d637f1e9f1ddeae5fa0f80bf6b3a331219bf9eea2982bc2d0d6f6",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="field-effect-scan-predecessor-f20",
        ),
        pytest.param(
            "1653f229ab703d24c7b2d12c2a485202a17a85d092c1ca8883f4d06e30b8427d",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="field-effect-scan-predecessor-f46",
        ),
        pytest.param(
            "817f9b8259502c8e51c538f7fc025198e9e9746f7f84f1b4bbbb44e0e7e3dbcd",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="pending-union-predecessor-f19a",
        ),
        pytest.param(
            "020300d05a69272d4d207d2659f7c33f1be4c7df7316b77220f55437e88ee32b",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="pending-union-predecessor-f20",
        ),
        pytest.param(
            "b331350eb1d34c6db9083d140cd83819a6dd4bb6df92b26ec97b60f7ded4f17b",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="pending-union-predecessor-f46",
        ),
        pytest.param(
            "d2a930fef7ff42cc2eb1ef2bc7ae9a0e804318c39b7b66647180f8f25919d4d9",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="case-index-predecessor-f19a",
        ),
        pytest.param(
            "e49e2c244459123efc7fa1e81ecb06e86b1ea223a3cd61b8fcaf9202005a2aed",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="case-index-predecessor-f20",
        ),
        pytest.param(
            "155f50cc6228c9ee65ae91b9aeaa6c9a5b12a18800a1c2c44910f1df114d9359",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="case-index-predecessor-f46",
        ),
        pytest.param(
            "5dddeb7bf21a0dfd7dabd3dfb6de59e85c705b349c229d9d40e15f0524e82d2e",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="claim-contention-predecessor-f19a",
        ),
        pytest.param(
            "96f08d12f6f4f7b6218c21e4525d25c4bdb5a37efdba3d3f71f38f0ebe02b285",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="claim-contention-predecessor-f20",
        ),
        pytest.param(
            "e01d177554d3ffabeada95d7296c4fe1fd98d83babd88140a79ed50d44c0263b",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="claim-contention-predecessor-f46",
        ),
        pytest.param(
            "00b19ce194b1747007f45990fa8412820082160294c21943064185f50bb05add",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="decision-scope-predecessor-f19a",
        ),
        pytest.param(
            "9e14250546e7d8a266d9f8661bbe5aa7965d0cfa8517a9a3fe1504c9f09d9a37",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="decision-scope-predecessor-f20",
        ),
        pytest.param(
            "f6bd788ba619c77b01ac543f62eab25eff12d6367bf74e89d83d871474720bd5",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="decision-scope-predecessor-f46",
        ),
        pytest.param(
            "38075caf63c051fb2031d6e894bcf7a462cfe5b66399ff8746b74ed5850bd5f1",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="incremental-witness-predecessor-f19a",
        ),
        pytest.param(
            "136b3f8178791e743eef56f47ebd7b7ad9bfbbfa371a3ffa1c3707de0a2c6e73",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="incremental-witness-predecessor-f20",
        ),
        pytest.param(
            "3557b4c3c5e83283579cc7e12826798363de63a41fe7a37abbae94adeec24962",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="incremental-witness-predecessor-f46",
        ),
        pytest.param(
            "4ba6c700429b90200d3b5a199224a5e8516a52ecc485142bac314ea4172c7d4b",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="activity-reprojection-predecessor-f19a",
        ),
        pytest.param(
            "df558e3560302dac323ccd6334d71e7e959b17aff547358a04a047453352e0b2",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="activity-reprojection-predecessor-f20",
        ),
        pytest.param(
            "7d53b02667577681193f7e31dfe0174808afdc163861a0304dab1dbaa940b9db",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="activity-reprojection-predecessor-f46",
        ),
        pytest.param(
            "a58b6124ab1e3969f736d6be5f334ec4bbde7fbb7d1092d46a0775ed58f74917",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="validated-document-reuse-predecessor-f19a",
        ),
        pytest.param(
            "c4b3c50781880100b9e9e3f35e7612a68a5ad4ecf2791a99a83c8f03928097bb",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="validated-document-reuse-predecessor-f20",
        ),
        pytest.param(
            "71bf91e84aa11d374361936959e0896d1e392e70f44d3222943da404fa47a056",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="validated-document-reuse-predecessor-f46",
        ),
        pytest.param(
            "a8b3c7313c303da3887bfff2c78dc94da9c9fb1479b942965e699455596543b1",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="pending-reads-predecessor-f19a",
        ),
        pytest.param(
            "c9f7ee31ee28a66f2dee3990b04a2e2feba6e2ba6e13d1db2a28a3f78dd2597b",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="pending-reads-predecessor-f20",
        ),
        pytest.param(
            "dc61060506cbafa838b607bdf2b816a37c7fb675e1fd20cbe59c2aa283cf045b",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="pending-reads-predecessor-f46",
        ),
        pytest.param(
            "4868a47c6c11039a80bb8282e650197feb42d6acf2d8159e4865fd8d95bc1fcf",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="activity-catch-up-predecessor-f19a",
        ),
        pytest.param(
            "07d23f668e1008b3d33d125e37c17cbd9c9a999d3a05d75ec51ea98b8fea5fd0",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="activity-catch-up-predecessor-f20",
        ),
        pytest.param(
            "0a84a82bbf9eb0e996c9146dd77d98b44a0488452287a28046c1c5033b0725ae",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="activity-catch-up-predecessor-f46",
        ),
        pytest.param(
            "2c441ef5ff534d16ab2be38cfdd5074c7d705f1418c95d90cc9431153b6fd9dc",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="attention-facts-predecessor-f19a",
        ),
        pytest.param(
            "e5256704d5fa042264ad22399628972180971767b6cbe156a47add3f16ea60e2",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="attention-facts-predecessor-f20",
        ),
        pytest.param(
            "3d55634c14b8cf83035539181795676290de80980f9c06519bcc0689abd06070",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="attention-facts-predecessor-f46",
        ),
        pytest.param(
            "6913ba37ecc710b36a7053a07aefc93dfa81173d88dce5b83bac5cb42db8168c",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="background-catch-up-predecessor-f19a",
        ),
        pytest.param(
            "1c6d8f7c4537f2ef496347983c353922d4a5237003551ed9904824aea96fd6f8",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="background-catch-up-predecessor-f20",
        ),
        pytest.param(
            "04f9941680479efbe74721fbc346c35700d49142036740a31fa19d0f954a233c",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="background-catch-up-predecessor-f46",
        ),
        pytest.param(
            "c56da1ff45f2749da3320c2737e7831247f877e9372996d662854a56a06ae00d",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
            id="currentness-memo-predecessor-f19a",
        ),
        pytest.param(
            "25c8b224c274dbe00636c5aca80401f8234ecbef7e3ad641129e863a0fb2deb1",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
            id="currentness-memo-predecessor-f20",
        ),
        pytest.param(
            "9a5dd89d0805be26d6e52f4a6223266a1128ab987b481ed985c462ebb09b0f1e",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
            id="currentness-memo-predecessor-f46",
        ),
    ),
)
def test_immediate_predecessor_decisions_preserve_only_their_frozen_permissions(
    digest, permissions
):
    all_permissions = (
        automatic_decision._HISTORICAL_PERMISSIONS_2
        | automatic_decision._HISTORICAL_PERMISSIONS_7
        | automatic_decision._HISTORICAL_PERMISSIONS_0
        | {"description_fill:unqualified"}
    )
    for identity in sorted(all_permissions):
        category, option = identity.split(":", 1)
        data = json.loads(
            _f19a_title_decision_document(digest, "replace_target:decoration_tidy")
        )
        core = {
            key: value
            for key, value in data.items()
            if key
            not in {
                "decision_id",
                "idempotency_digest",
                "occurred_at",
                "origin",
                "schema_version",
            }
        }
        core.update(
            policy_category=category,
            policy_option=option,
            bundle_id="automatic_bundle_" + "4" * 64
            if category in {"title_layout", "date_precision"}
            else None,
        )
        data.update(
            core,
            decision_id="automatic_decision_" + _digest({**core, "occurred_at": 1}),
            idempotency_digest=_digest(core),
        )
        document = json.dumps(data, separators=(",", ":"), sort_keys=True)
        assert not qualification_manifest_is_current(category, option, digest)
        if identity in permissions:
            assert AutomaticDecision.from_json(document).to_json() == document
            with pytest.raises(AutomaticDecisionError):
                AutomaticDecision.create(**core, occurred_at=1)
        else:
            with pytest.raises(AutomaticDecisionError):
                AutomaticDecision.from_json(document)


@pytest.mark.parametrize(
    "option",
    (
        "preserve:audiobook_title_fill",
        "preserve:audiobook_title_replace",
        "title_and_subtitle:audiobook_title_fill",
        "title_and_subtitle:audiobook_title_replace",
    ),
)
def test_scalar_audiobook_title_witness_is_closed_and_evidence_bound(option):
    from types import SimpleNamespace

    from mechanical_librarian.domain.automatic_decision import (
        audiobook_title_candidate_bundle,
    )

    values = dict(
        destination="title",
        policy_category="title_layout",
        policy_option=option,
        policy_destination="automatic",
        disposition="candidate",
        candidate_id="candidate-one",
        combination_ids=(),
        citation_ids=("citation-one",),
        reason_code="unique_supported_candidate",
        outcome_id="outcome-one",
    )
    bundle = audiobook_title_candidate_bundle(SimpleNamespace(**values))
    assert bundle == "automatic_bundle_" + _digest(
        {
            "kind": "audiobook_title_candidate_v1",
            "source_outcome_id": "outcome-one",
            "candidate_id": "candidate-one",
            "citation_ids": ["citation-one"],
        }
    )
    for change in (
        {"destination": "subtitle"},
        {"policy_category": "subtitle"},
        {"policy_option": "preserve"},
        {"policy_option": "preserve:audiobook_title_preserve"},
        {"policy_destination": "ask"},
        {"disposition": "keep_current"},
        {"candidate_id": None},
        {"combination_ids": ("combination",)},
        {"citation_ids": ()},
        {"reason_code": "target_only_preservation"},
    ):
        assert (
            audiobook_title_candidate_bundle(SimpleNamespace(**(values | change)))
            is None
        )
    for change in (
        {"candidate_id": "candidate-two"},
        {"outcome_id": "outcome-two"},
        {"citation_ids": ("citation-two",)},
    ):
        assert (
            audiobook_title_candidate_bundle(SimpleNamespace(**(values | change)))
            != bundle
        )


@pytest.mark.parametrize(
    "fixture_name", ["activity_read_predecessors", "activity_permission_predecessors"]
)
@pytest.mark.parametrize("family", ["f19a", "f20", "f46"])
def test_activity_read_predecessor_keeps_exact_decode_only_permissions(
    family: str,
    fixture_name: str,
) -> None:
    fixture = Path(__file__).parents[1] / "support" / fixture_name
    provenance = json.loads((fixture / "provenance.json").read_text())
    record = provenance["records"][family]
    document = (fixture / (family + ".json")).read_text().rstrip("\n")
    assert hashlib.sha256(document.encode()).hexdigest() == record["document_sha256"]
    decision = AutomaticDecision.from_json(document)
    assert decision.to_json() == document
    histories = getattr(
        automatic_decision, "_PRIOR_" + family.upper() + "_QUALIFICATION_MANIFESTS"
    )
    assert dict(histories)[record["manifest_sha256"]] == frozenset(
        record["permissions"]
    )
    assert not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    )
    arguments = json.loads(document)
    for name in ("decision_id", "idempotency_digest", "origin", "schema_version"):
        arguments.pop(name)
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(**arguments)


@pytest.mark.parametrize(
    "family,digest,permissions",
    [
        (
            "F19A",
            "b0987ca2be0c2171545da8942b1646a99197e1315714e985b07049b637ca63b3",
            automatic_decision._HISTORICAL_PERMISSIONS_2,
        ),
        (
            "F20",
            "013fd7173f7b748d900e9088a0955c857f1aeb3543fc630d1f2f792d6d8e1a59",
            automatic_decision._HISTORICAL_PERMISSIONS_7,
        ),
        (
            "F46",
            "c242735d3aa59e823ed9db00968828acd6a3a6a536c5b72d2ca4fbe644ddfd06",
            automatic_decision._HISTORICAL_PERMISSIONS_0,
        ),
    ],
)
def test_connection_startup_predecessors_are_readable_but_never_current(
    family, digest, permissions
):
    bindings = dict(
        getattr(automatic_decision, "_PRIOR_" + family + "_QUALIFICATION_MANIFESTS")
    )
    assert bindings[digest] == permissions
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "support"
            / "retained_qualification_predecessors"
            / "connection_startup_decisions.json"
        ).read_text()
    )
    case = next(case for case in fixture["cases"] if case["family"] == family)
    assert case["decisions"]
    for document in case["decisions"]:
        decision = AutomaticDecision.from_json(document)
        assert decision.qualification_manifest_sha256 == digest
        assert decision.to_json() == document
        assert not qualification_manifest_is_current(
            decision.policy_category, decision.policy_option, digest
        )
    for identity in permissions:
        category, option = identity.split(":", 1)
        assert not qualification_manifest_is_current(category, option, digest)
    if family == "F46":
        assert (
            audiobook_decision_qualification.F46_COVER_FILL_IDENTITY not in permissions
        )


def test_actual_provider_recovery_base_decisions_remain_historical_only() -> None:
    fixture = json.loads(
        Path(
            "tests/fixtures/provider-recovery-base-decisions-25e2a3b6.json"
        ).read_text()
    )
    assert fixture["source_sha"] == "25e2a3b604ec1d82e7ee100c6bc946cab859b37d"
    assert set(fixture["decisions"]) == {"f19a", "f20", "f46"}
    for document in fixture["decisions"].values():
        decision = AutomaticDecision.from_json(document)
        assert decision.to_json() == document
        assert not qualification_manifest_is_current(
            decision.policy_category,
            decision.policy_option,
            decision.qualification_manifest_sha256,
        )
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(**decision._core(), occurred_at=2)
