from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mechanical_librarian.domain import (
    audiobook_decision_qualification,
    field_value_comparison,
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


@pytest.mark.parametrize(
    "category,option,destination,current",
    [
        ("title", "replace_target:decoration_tidy", "title", "F19A"),
        ("genres", "add", "genres", "F20"),
        (
            *F46_SERIES_IDENTITY.split(":", 1),
            "series",
            "F46",
        ),
    ],
)
def test_decision_carrying_any_noncurrent_digest_is_rejected(
    category: str, option: str, destination: str, current: str
) -> None:
    """Only the current digest decodes; ADR-0087 keeps no decoding history."""
    digests = {
        "F19A": F19A_QUALIFICATION_MANIFEST_SHA256,
        "F20": F20_QUALIFICATION_CANDIDATE_SHA256,
        "F46": audiobook_decision_qualification.F46_QUALIFICATION_MANIFEST_SHA256,
    }
    core = {
        "bundle_id": None,
        "candidate_id": "alternative_" + "f" * 64,
        "destination": destination,
        "outcome": "candidate",
        "policy_category": category,
        "policy_generation": 1,
        "policy_option": option,
        "proposal_id": "review_proposal_" + "d" * 64,
        "proposal_set_id": "proposal_set_" + "c" * 64,
        "report_id": "report_" + "b" * 64,
        "resolved_policy_id": "resolved_policy_" + "0" * 64,
        "review_case_id": "review_case_" + "a" * 64,
        "rule_id": "shadow_rule_" + "1" * 64,
        "source_outcome_id": "shadow_outcome_" + "3" * 64,
        "target_fingerprint": "e" * 64,
    }
    for digest in (
        *digests.values(),
        F38_QUALIFICATION_MANIFEST_SHA256,
        # The pre-ADR-0087 digests of F19a, F20 and F46.
        "e7c357aae9261096bc4a7e16e536aab2e9eee033eb798ecee05a7c85a227a1de",
        "df47dad078d56d9ae6ab52557aa055cdd3f4dde5d1ac1e42236e708bd38cab47",
        "ac5d952d6be0cf02be448f73873d6bc1912b898dc065bc8f8e2f41b02a5bea26",
        "9" * 64,
    ):
        bound = {**core, "qualification_manifest_sha256": digest}
        document = json.dumps(
            {
                **bound,
                "decision_id": "automatic_decision_"
                + _digest({**bound, "occurred_at": 1}),
                "idempotency_digest": _digest(bound),
                "occurred_at": 1,
                "origin": "automatic_policy",
                "schema_version": "automatic-decision-v1",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        if digest == digests[current]:
            assert AutomaticDecision.from_json(document).to_json() == document
        else:
            with pytest.raises(AutomaticDecisionError):
                AutomaticDecision.from_json(document)


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
def test_captured_base_producer_decisions_are_rejected(name: str, digest: str) -> None:
    document = (
        Path(__file__).parents[1]
        / "support/automatic_decision_predecessors"
        / f"{name}-before-f39.json"
    ).read_bytes()
    assert hashlib.sha256(document).hexdigest() == digest
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.from_json(document.decode())


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
    # Rotating the record refuses the persisted decision outright, exactly as
    # an ebook rotation does: no decoding history is kept (ADR-0087).
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_QUALIFICATION_MANIFEST_SHA256",
        "8" * 64,
    )
    assert not qualification_manifest_is_current(category, option, record)
    assert qualification_manifest_is_current(category, option, "8" * 64)
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


def test_actual_predecessor_producer_decisions_are_rejected() -> None:
    support = Path(__file__).parents[1] / "support"
    documents = [
        (support / name / (family + ".json")).read_text().rstrip("\n")
        for name in ("activity_read_predecessors", "activity_permission_predecessors")
        for family in ("f19a", "f20", "f46")
    ]
    startup = json.loads(
        (
            support
            / "retained_qualification_predecessors"
            / "connection_startup_decisions.json"
        ).read_text()
    )
    documents += [d for case in startup["cases"] for d in case["decisions"]]
    recovery = json.loads(
        Path(
            "tests/fixtures/provider-recovery-base-decisions-25e2a3b6.json"
        ).read_text()
    )
    assert recovery["source_sha"] == "25e2a3b604ec1d82e7ee100c6bc946cab859b37d"
    documents += recovery["decisions"].values()
    assert len(documents) >= 12
    for document in documents:
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.from_json(document)


@pytest.mark.parametrize(
    ("values", "format_only"),
    (
        (["Audiobook"], True),
        (("Unabridged", "(Audio Book)", " AUDIOBOOKS "), True),
        (["Audible Audiobook", "MP3 CD"], True),
        (["Audiobook", "Fantasy"], False),
        (["Audiobook of the year"], False),
        (["(Audiobook"], False),
        ([], False),
        (None, False),
        ("Audiobook", False),
        ([1], False),
    ),
)
def test_format_only_genres_name_only_the_recording_format(values, format_only):
    assert field_value_comparison.audiobook_format_only_genres(values) is format_only


@pytest.mark.parametrize(
    ("option", "current", "desired", "identity"),
    (
        (
            "fill_empty",
            ["Audiobook"],
            ["Fantasy"],
            "genres:fill_empty:audiobook_genres_fill",
        ),
        ("fill_empty", ["Audiobook", "Fantasy"], ["Fantasy"], None),
        (
            "fill_empty",
            ["Audiobook"],
            ["Audiobook"],
            "genres:fill_empty:audiobook_genres_preserve",
        ),
        (
            "add",
            ["Audiobook"],
            ["Audiobook", "Fantasy"],
            "genres:add:audiobook_genres_fill",
        ),
        (
            "replace",
            ["Unabridged"],
            ["Fantasy"],
            "genres:replace:audiobook_genres_fill",
        ),
        (
            "replace",
            ["Mystery"],
            ["Fantasy"],
            "genres:replace:audiobook_genres_replace",
        ),
    ),
)
def test_a_format_only_genre_incumbent_is_filled_like_an_empty_one(
    option, current, desired, identity
):
    """The `language_is_unknown` precedent, applied to genres (#1250)."""
    assert (
        audiobook_decision_qualification.identity_for_effect(
            "genres", "genres", option, current, desired
        )
        == identity
    )


def test_persisted_genre_reconstruction_fills_a_format_only_incumbent():
    from mechanical_librarian.domain.rich_metadata_preview import (
        RichMetadataPreviewError,
        project_audiobook_automatic_decision_value,
    )

    candidate = {"kind": "list", "value": ["Fantasy"]}
    assert (
        project_audiobook_automatic_decision_value(
            "genres",
            "genres",
            "fill_empty:audiobook_genres_fill",
            candidate,
            ["Audiobook"],
        )
        == candidate
    )
    with pytest.raises(RichMetadataPreviewError, match="unqualified"):
        project_audiobook_automatic_decision_value(
            "genres",
            "genres",
            "fill_empty:audiobook_genres_fill",
            candidate,
            ["Audiobook", "Mystery"],
        )
    with pytest.raises(RichMetadataPreviewError, match="invalid"):
        project_audiobook_automatic_decision_value(
            "genres",
            "genres",
            "fill_empty:audiobook_genres_fill",
            {"kind": "list", "value": ["Unabridged", "Audiobook"]},
            None,
        )
