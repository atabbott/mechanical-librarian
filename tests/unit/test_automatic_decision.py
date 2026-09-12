from __future__ import annotations

import hashlib
import json

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

    assert identity not in QUALIFIED_POLICY_OPTIONS
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


def test_each_historical_manifest_digest_has_one_frozen_option_binding():
    for rows in (
        automatic_decision._PRIOR_F19A_QUALIFICATION_MANIFESTS,
        automatic_decision._PRIOR_F20_QUALIFICATION_MANIFESTS,
    ):
        assert len(rows) == len({digest for digest, _ in rows})
    rows = dict(automatic_decision._PRIOR_F19A_QUALIFICATION_MANIFESTS)
    for digest in (
        "91292dd98bef221efc6d75ed336bc1031e3917fa75c05dcd38474eef5b29e523",
        "010c06261591a6f4fd959c8c1fe0cc8e1346b3fc58a7923daf4de1ec93049cae",
    ):
        assert (
            rows[digest] == automatic_decision._PRE_1007_F19A_QUALIFIED_POLICY_OPTIONS
        )
