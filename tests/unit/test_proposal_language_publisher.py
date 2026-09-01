"""Proposal language and publisher policy: equivalence-class grouping,
alias mapping, base/script contradictions, distinct publisher
business levels, and manifestation-bound anchors.
"""

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    IdentifierRelation,
    LocalClaim,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAssessment,
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _explicit_local_ebook_isbn,
    _fields,
    _manifestation_publisher_provider,
    _provider,
    _publication_local_isbn,
    _report,
    _result,
)


def test_language_policy_preserves_supported_regional_target() -> None:
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.VALUE,
            "en-US",
        )
    )
    providers = (
        _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "en",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
        _provider(
            "beta",
            "beta-edition",
            (
                _claim(
                    "beta",
                    "beta-edition",
                    ClaimKind.LANGUAGE,
                    "eng",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
    )

    report = _report(fields=fields, providers=providers)
    proposal_set = evaluate_report(report)
    result = _result(proposal_set, TargetDestination.LANGUAGE)

    assert ProposalOutcome.KEEP == result.outcome
    assert ("field_policy_preservation",) == result.reasons
    assert result.selected_alternative_id is None
    assert 1 == len(result.alternatives)
    assert "en" == result.alternatives[0].value.value
    assert ProposalAssessment.PRESERVES_TARGET is result.alternatives[0].assessment
    assert 2 == len(result.alternatives[0].evidence_references)
    assert 1 == len(result.alternatives[0].correlations)
    assert {"en", "eng"} == {
        claim.value
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.LANGUAGE
    }


def test_language_policy_strips_region_before_grouping() -> None:
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.VALUE,
            "en-US",
        )
    )
    providers = (
        _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "en",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
        _provider(
            "beta",
            "beta-edition",
            (
                _claim(
                    "beta",
                    "beta-edition",
                    ClaimKind.LANGUAGE,
                    "en-GB",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
    )

    result = _result(
        evaluate_report(_report(fields=fields, providers=providers)),
        TargetDestination.LANGUAGE,
    )

    assert ProposalOutcome.KEEP == result.outcome
    assert ("field_policy_preservation",) == result.reasons
    assert 1 == len(result.alternatives)
    assert ProposalAssessment.PRESERVES_TARGET is result.alternatives[0].assessment
    assert 2 == len(result.alternatives[0].evidence_references)
    assert 1 == len(result.alternatives[0].correlations)


_TARGET_DETAIL_CONTROLS = (
    ("script", "en-Latn", "en-Cyrl"),
    ("unspecified_script", "en", "en-Latn"),
)


@pytest.mark.parametrize(
    ("target", "candidate"),
    tuple(
        pytest.param(*control[1:], id=control[0]) for control in _TARGET_DETAIL_CONTROLS
    ),
)
def test_language_policy_rejects_sole_target_detail_contradiction(
    target, candidate
) -> None:
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.VALUE,
            target,
        )
    )
    providers = tuple(
        _provider(
            provider,
            f"{provider}-edition",
            (
                _claim(
                    provider,
                    f"{provider}-edition",
                    ClaimKind.LANGUAGE,
                    candidate,
                    scope=ClaimScope.EDITION,
                ),
            ),
            authority=authority,
        )
        for provider, authority in (
            ("hardcover", "hardcover"),
            ("open_library", "open_library"),
        )
    )

    result = _result(
        evaluate_report(_report(fields=fields, providers=providers)),
        TargetDestination.LANGUAGE,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("field_policy_conflict",) == result.reasons
    assert 1 == len(result.alternatives)
    assert ProposalAssessment.REPLACES_TARGET == result.alternatives[0].assessment
    assert 2 == len(result.alternatives[0].evidence_references)
    assert 2 == len(result.alternatives[0].correlations)


def test_language_policy_keeps_unspecified_and_specific_scripts_distinct() -> None:
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.VALUE,
            "en-Latn-US",
        )
    )
    provider = _provider(
        "alpha",
        "alpha-edition",
        (
            _claim(
                "alpha",
                "alpha-edition",
                ClaimKind.LANGUAGE,
                "eng",
                scope=ClaimScope.EDITION,
            ),
        ),
    )

    result = _result(
        evaluate_report(_report(fields=fields, providers=(provider,))),
        TargetDestination.LANGUAGE,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("field_policy_conflict",) == result.reasons
    assert 1 == len(result.alternatives)
    assert ProposalAssessment.REPLACES_TARGET == result.alternatives[0].assessment


def test_equivalent_language_candidates_form_one_unique_candidate() -> None:
    providers = (
        _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "en",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
        _provider(
            "beta",
            "beta-edition",
            (
                _claim(
                    "beta",
                    "beta-edition",
                    ClaimKind.LANGUAGE,
                    "eng",
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
    )
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.MISSING,
        )
    )

    proposal_set = evaluate_report(_report(fields=fields, providers=providers))
    result = _result(proposal_set, TargetDestination.LANGUAGE)

    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert ("unique_typed_candidate",) == result.reasons
    assert 1 == len(result.alternatives)
    assert result.alternatives[0].alternative_id == result.selected_alternative_id
    assert "en" == result.alternatives[0].value.value
    assert 2 == len(result.alternatives[0].evidence_references)
    assert 1 == len(result.alternatives[0].correlations)
    assert proposal_set == ProposalSet.from_json(proposal_set.to_json())


def test_deprecated_bh_language_alias_maps_to_favored_bih() -> None:
    providers = tuple(
        _provider(
            provider,
            f"{provider}-edition",
            (
                _claim(
                    provider,
                    f"{provider}-edition",
                    ClaimKind.LANGUAGE,
                    value,
                    scope=ClaimScope.EDITION,
                ),
            ),
        )
        for provider, value in (("alpha", "bh"), ("beta", "bih"))
    )
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.MISSING,
        )
    )

    proposal_set = evaluate_report(_report(fields=fields, providers=providers))
    result = _result(proposal_set, TargetDestination.LANGUAGE)

    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert 1 == len(result.alternatives)
    assert result.alternatives[0].alternative_id == result.selected_alternative_id
    assert "bh" == result.alternatives[0].value.value
    assert 2 == len(result.alternatives[0].evidence_references)
    assert 1 == len(result.alternatives[0].correlations)
    assert proposal_set == ProposalSet.from_json(proposal_set.to_json())


_LANGUAGE_CONFLICT_CONTROLS = (
    ("different_base", "fr", "eng"),
    ("different_script", "en-Latn", "en-Cyrl"),
    ("unspecified_script", "en", "en-Latn"),
    ("malformed", "en", "english"),
)


@pytest.mark.parametrize(
    ("left", "right"),
    tuple(
        pytest.param(*control[1:], id=control[0])
        for control in _LANGUAGE_CONFLICT_CONTROLS
    ),
)
def test_language_policy_rejects_base_script_and_malformed_conflicts(
    left, right
) -> None:
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.MISSING,
        )
    )
    providers = (
        _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    left,
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
        _provider(
            "beta",
            "beta-edition",
            (
                _claim(
                    "beta",
                    "beta-edition",
                    ClaimKind.LANGUAGE,
                    right,
                    scope=ClaimScope.EDITION,
                ),
            ),
        ),
    )
    result = _result(
        evaluate_report(_report(fields=fields, providers=providers)),
        TargetDestination.LANGUAGE,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("field_policy_conflict",) == result.reasons

    single_malformed = _provider(
        "alpha",
        "alpha-edition",
        (
            _claim(
                "alpha",
                "alpha-edition",
                ClaimKind.LANGUAGE,
                "english",
                scope=ClaimScope.EDITION,
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                providers=(single_malformed,),
            )
        ),
        TargetDestination.LANGUAGE,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("field_policy_conflict",) == result.reasons


_PRIVATE_USE_CONTROLS = (
    (
        "private_use",
        "en-AA",
        ProposalOutcome.UNRESOLVED_CHOICE,
        ("field_policy_conflict",),
        False,
    ),
    (
        "registered",
        "en-US",
        ProposalOutcome.PROPOSE_CHANGE,
        ("unique_typed_candidate",),
        True,
    ),
)


@pytest.mark.parametrize(
    ("value", "outcome", "reasons", "selected"),
    tuple(
        pytest.param(*control[1:], id=control[0]) for control in _PRIVATE_USE_CONTROLS
    ),
)
def test_private_use_language_region_cannot_be_selected(
    value, outcome, reasons, selected
) -> None:
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.MISSING,
        )
    )
    provider = _provider(
        "catalogue",
        "edition-1",
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.LANGUAGE,
                value,
                scope=ClaimScope.EDITION,
            ),
        ),
    )

    result = _result(
        evaluate_report(_report(fields=fields, providers=(provider,))),
        TargetDestination.LANGUAGE,
    )

    assert outcome == result.outcome
    assert reasons == result.reasons
    assert value == result.alternatives[0].value.value
    assert selected == (result.selected_alternative_id is not None)


def test_publisher_policy_keeps_distinct_supported_business_levels() -> None:
    local_publisher = LocalClaim.create(
        kind=ClaimKind.PUBLISHER,
        observed="Tor Books",
        canonical="Tor Books",
        excerpt="Tor Books",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    providers = (
        _manifestation_publisher_provider(
            "alpha",
            "alpha-edition",
            ClaimKind.IMPRINT,
            "Tor",
        ),
        _manifestation_publisher_provider(
            "beta",
            "beta-edition",
            ClaimKind.PUBLISHER,
            "Macmillan Publishing Group",
        ),
    )
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.VALUE,
            "Tor Books",
        )
    )

    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                local_claims=(
                    _explicit_local_ebook_isbn(),
                    local_publisher,
                ),
                providers=providers,
            )
        ),
        TargetDestination.PUBLISHER,
    )

    assert ProposalOutcome.KEEP == result.outcome
    assert ("field_policy_preservation",) == result.reasons
    assert {"Tor Books", "Tor", "Macmillan Publishing Group"} == {
        alternative.value.value for alternative in result.alternatives
    }
    references = {
        reference
        for alternative in result.alternatives
        for reference in alternative.evidence_references
    }
    assert local_publisher.reference in references
    assert {
        claim.reference
        for provider in providers
        for claim in provider.candidates[0].claims
        if claim.kind in {ClaimKind.PUBLISHER, ClaimKind.IMPRINT}
    }.issubset(references)


def test_distinct_publishers_without_a_supported_target_are_acceptable_choice() -> None:
    providers = (
        _manifestation_publisher_provider(
            "alpha",
            "alpha-edition",
            ClaimKind.IMPRINT,
            "Tor",
        ),
        _manifestation_publisher_provider(
            "beta",
            "beta-edition",
            ClaimKind.PUBLISHER,
            "Macmillan",
        ),
    )
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.MISSING,
        )
    )

    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                local_claims=(_explicit_local_ebook_isbn(),),
                providers=providers,
            )
        ),
        TargetDestination.PUBLISHER,
    )

    assert ProposalOutcome.ACCEPTABLE_CHOICE == result.outcome
    assert ("field_policy_acceptable_choice",) == result.reasons
    assert result.selected_alternative_id is None
    assert {"Tor", "Macmillan"} == {
        alternative.value.value for alternative in result.alternatives
    }


def test_publisher_policy_requires_valid_manifestation_bound_evidence() -> None:
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.MISSING,
        )
    )
    invalid_or_wrong_scope = (
        _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                ),
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.PUBLISHER,
                    "<not-a-publisher>",
                    scope=ClaimScope.EDITION,
                    order=1,
                ),
            ),
        ),
        _provider(
            "beta",
            "beta-work",
            (
                _claim(
                    "beta",
                    "beta-work",
                    ClaimKind.PUBLISHER,
                    "Associated Work Publisher",
                    scope=ClaimScope.WORK,
                ),
            ),
        ),
    )

    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                local_claims=(_explicit_local_ebook_isbn(),),
                providers=invalid_or_wrong_scope,
            )
        ),
        TargetDestination.PUBLISHER,
    )

    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_publisher_policy_does_not_treat_target_syntax_as_evidence() -> None:
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.VALUE,
            "Macmillan",
        )
    )
    provider = _manifestation_publisher_provider(
        "alpha",
        "alpha-edition",
        ClaimKind.PUBLISHER,
        "Tor Books",
    )

    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                local_claims=(_explicit_local_ebook_isbn(),),
                providers=(provider,),
            )
        ),
        TargetDestination.PUBLISHER,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("insufficient_independent_corroboration",) == result.reasons


def test_publisher_policy_excludes_wrong_manifestation_provider() -> None:
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.MISSING,
        )
    )
    provider = _manifestation_publisher_provider(
        "alpha",
        "unrelated-edition",
        ClaimKind.PUBLISHER,
        "Wrong Manifestation Press",
        isbn="9783161484100",
    )

    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                local_claims=(_explicit_local_ebook_isbn(),),
                providers=(provider,),
            )
        ),
        TargetDestination.PUBLISHER,
    )

    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


_PUBLISHER_ANCHOR_CONTROLS = (
    (
        "unresolved",
        (_publication_local_isbn(),),
        "unknown",
        IdentifierRelation.UNRESOLVED,
        ProposalOutcome.INELIGIBLE,
    ),
    (
        "associated",
        (_explicit_local_ebook_isbn(),),
        "unknown",
        IdentifierRelation.ASSOCIATED,
        ProposalOutcome.INELIGIBLE,
    ),
    (
        "accepted",
        (_explicit_local_ebook_isbn(),),
        "ebook",
        IdentifierRelation.ACCEPTED,
        ProposalOutcome.PROPOSE_CHANGE,
    ),
)


@pytest.mark.parametrize(
    ("local_claims", "applicability", "relation", "outcome"),
    tuple(
        pytest.param(*control[1:], id=control[0])
        for control in _PUBLISHER_ANCHOR_CONTROLS
    ),
)
def test_publisher_provider_requires_accepted_identifier_anchor(
    local_claims, applicability, relation, outcome
) -> None:
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.MISSING,
        )
    )
    provider = _manifestation_publisher_provider(
        "catalogue",
        "edition-1",
        ClaimKind.PUBLISHER,
        "Bound Press",
        applicability=applicability,
    )
    report = _report(
        fields=fields,
        local_claims=local_claims,
        providers=(provider,),
    )
    provider_relations = {
        evidence.relation
        for evidence in report.identifier_evidence
        if evidence.provider == "catalogue"
        and evidence.candidate_identity == "edition-1"
    }

    result = _result(
        evaluate_report(report),
        TargetDestination.PUBLISHER,
    )

    assert {relation} == provider_relations
    assert outcome == result.outcome
    assert (
        ("Bound Press",) if outcome is ProposalOutcome.PROPOSE_CHANGE else ()
    ) == tuple(alternative.value.value for alternative in result.alternatives)


def test_publisher_anchor_must_reference_accepted_edition_isbn() -> None:
    provider = _provider(
        "catalogue",
        "edition-1",
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="unknown",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.WORK,
                applicability="ebook",
                order=1,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.PUBLISHER,
                "Wrong Scope Press",
                scope=ClaimScope.EDITION,
                order=2,
            ),
        ),
    )
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.MISSING,
        )
    )
    report = _report(
        fields=fields,
        local_claims=(_explicit_local_ebook_isbn(),),
        providers=(provider,),
    )
    isbn_claims = {
        claim.reference: claim
        for claim in provider.candidates[0].claims
        if claim.kind is ClaimKind.ISBN
    }
    provider_evidence = {
        (
            isbn_claims[evidence.reference].scope,
            isbn_claims[evidence.reference].applicability,
        ): evidence.relation
        for evidence in report.identifier_evidence
        if evidence.provider == "catalogue"
    }

    result = _result(
        evaluate_report(report),
        TargetDestination.PUBLISHER,
    )

    assert {
        (ClaimScope.EDITION, "unknown"): IdentifierRelation.ASSOCIATED,
        (ClaimScope.WORK, "ebook"): IdentifierRelation.ACCEPTED,
    } == provider_evidence
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_selected_epub_publisher_does_not_need_provider_anchor() -> None:
    local_publisher = LocalClaim.create(
        kind=ClaimKind.PUBLISHER,
        observed="Selected EPUB Press",
        canonical="Selected EPUB Press",
        excerpt="Selected EPUB Press",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    fields = _fields(
        publisher=TargetField(
            TargetDestination.PUBLISHER,
            TargetFieldState.MISSING,
        )
    )

    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                local_claims=(local_publisher,),
            )
        ),
        TargetDestination.PUBLISHER,
    )

    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert "Selected EPUB Press" == result.alternatives[0].value.value
    assert (local_publisher.reference,) == result.alternatives[0].evidence_references
