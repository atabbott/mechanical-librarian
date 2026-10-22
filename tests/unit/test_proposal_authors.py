"""Proposal authors and narrators: complete-list requirements, typed
non-author credits, identity-bound aliases, and the controlled
package-creator placeholder.
"""

import pytest

from mechanical_librarian.domain import proposal_set
from mechanical_librarian.domain.field_value_comparison import creator_list_reordered
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAssessment,
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _identity_bound_alias_report,
    _local_creator,
    _partially_bound_direct_author_report,
    _provider,
    _report,
    _result,
)


def test_complete_authors_never_drop_a_coauthor() -> None:
    authors = TargetField(
        TargetDestination.AUTHORS,
        TargetFieldState.VALUE,
        ("Ada Author", "Bea Author"),
    )
    claim = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.CREATOR,
        "Ada Author",
        scope=ClaimScope.WORK,
        role="author",
    )
    proposal_set = evaluate_report(
        _report(
            fields=_fields(authors=authors),
            providers=(_provider("catalogue", "edition-1", (claim,)),),
        )
    )
    result = _result(proposal_set, TargetDestination.AUTHORS)
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert "insufficient_independent_corroboration" in result.reasons


def test_duplicate_creator_sets_do_not_become_an_authors_proposal() -> None:
    local_claims = (
        _local_creator("Ada Author", occurrence=0),
        _local_creator("ADA  AUTHOR", occurrence=1),
    )
    provider_claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "Ada Author",
            scope=ClaimScope.WORK,
            role="author",
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "ADA  AUTHOR",
            scope=ClaimScope.WORK,
            role="author",
            order=1,
        ),
    )
    candidate = finalize_candidate_comparisons(
        ProviderCandidate(
            "edition-1",
            "catalogue-work",
            provider_claims,
        ),
        local_claims,
    )
    result = _result(
        evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=local_claims,
                providers=(
                    ProviderObservation(
                        "catalogue",
                        ProviderStatus.CANDIDATES,
                        candidates=(candidate,),
                    ),
                ),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_identity_bound_aliases_do_not_grant_three_author_authority() -> None:
    report = _identity_bound_alias_report()
    creator_claims = tuple(
        claim
        for claim in report.providers[0].candidates[0].claims
        if claim.kind is ClaimKind.CREATOR
    )
    assert all(
        any(link.outcome is Comparison.AGREEMENT for link in claim.links)
        for claim in creator_claims
    )
    result = _result(evaluate_report(report), TargetDestination.AUTHORS)
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert (("Ada Example", "Bob Example"),) == tuple(
        alternative.value.value for alternative in result.alternatives
    )
    assert all(
        reference.startswith("local_")
        for alternative in result.alternatives
        for reference in alternative.evidence_references
    )


def test_incomplete_identity_bindings_preserve_direct_author_evidence() -> None:
    report = _partially_bound_direct_author_report()
    provider_references = {
        claim.reference
        for claim in report.providers[0].candidates[0].claims
        if claim.kind is ClaimKind.CREATOR
    }
    result = _result(evaluate_report(report), TargetDestination.AUTHORS)
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert (("Ada Example", "Bob Example"),) == tuple(
        alternative.value.value for alternative in result.alternatives
    )
    assert provider_references.issubset(
        {
            reference
            for alternative in result.alternatives
            for reference in alternative.evidence_references
        }
    )


def test_provider_typed_non_author_credits_preserve_complete_authors() -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "Ada Author",
            scope=ClaimScope.WORK,
            role="author",
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "Bea Author",
            scope=ClaimScope.WORK,
            role="author",
            order=1,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CONTRIBUTOR,
            "Ed Editor",
            scope=ClaimScope.WORK,
            role="editor",
            order=2,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CONTRIBUTOR,
            "Ivy Illustrator",
            scope=ClaimScope.WORK,
            role="illustrator",
            order=3,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert ("Ada Author", "Bea Author") == result.alternatives[0].value.value


@pytest.mark.parametrize(
    "role",
    (None, "author", "pseudonym", "collective"),
    ids=("unspecified", "author", "pseudonym", "collective"),
)
def test_ambiguous_provider_author_credits_do_not_become_partial_authors(
    role: str | None,
) -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "Ada Author",
            scope=ClaimScope.WORK,
            role="author",
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CONTRIBUTOR,
            "Ambiguous Credit",
            scope=ClaimScope.WORK,
            role=role,
            order=1,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


@pytest.mark.parametrize(
    "state",
    (FieldState.MALFORMED, FieldState.OVERSIZED),
    ids=("malformed", "oversized"),
)
def test_incomplete_provider_author_list_is_withheld(state: FieldState) -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "Ada Author",
            scope=ClaimScope.WORK,
            role="author",
        ),
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.CREATOR,
            value=None,
            state=state,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=1,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


@pytest.mark.parametrize(
    "role",
    ("author", "pseudonym", "collective", None),
    ids=("author", "pseudonym", "collective", "unspecified"),
)
@pytest.mark.parametrize(
    "source",
    ("local", "provider"),
    ids=("local", "provider"),
)
def test_unrepresentable_author_like_or_unknown_contributor_withholds_authors(
    source: str,
    role: str | None,
) -> None:
    claims: tuple[LocalClaim, ...] | tuple[ProviderClaim, ...]
    if source == "local":
        claims = (
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed="Ada Author",
                canonical="Ada Author",
                excerpt="Ada Author",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                role="author",
            ),
            LocalClaim.create(
                kind=ClaimKind.CONTRIBUTOR,
                observed="Unrepresentable Credit",
                canonical=None,
                excerpt="Unrepresentable Credit",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                state=FieldState.MALFORMED,
                role=role,
                occurrence=1,
            ),
        )
        proposal_set = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=claims,
            )
        )
    else:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "Ada Author",
                scope=ClaimScope.WORK,
                role="author",
            ),
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.CONTRIBUTOR,
                value=None,
                state=FieldState.MALFORMED,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=role,
                raw_order=1,
            ),
        )
        proposal_set = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        )
    result = _result(proposal_set, TargetDestination.AUTHORS)
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_equivalent_observations_coalesce_with_a_deterministic_display() -> None:
    providers = (
        _provider(
            "hardcover",
            "edition-1",
            (
                _claim(
                    "hardcover",
                    "edition-1",
                    ClaimKind.TITLE,
                    "foo bar",
                    scope=ClaimScope.WORK,
                ),
            ),
            authority="hardcover",
        ),
        _provider(
            "open_library",
            "edition-2",
            (
                _claim(
                    "open_library",
                    "edition-2",
                    ClaimKind.TITLE,
                    "Foo  Bar",
                    scope=ClaimScope.WORK,
                ),
            ),
            authority="open_library",
        ),
    )
    result = _result(
        evaluate_report(_report(title=None, providers=providers)),
        TargetDestination.TITLE,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert 1 == len(result.alternatives)
    assert "Foo Bar" == result.alternatives[0].value.value
    assert 2 == len(result.alternatives[0].correlations)


def test_ambiguous_local_creator_does_not_become_partial_authors() -> None:
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed=name,
            canonical=name,
            excerpt=name,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            role=role,
            occurrence=index,
        )
        for index, (name, role) in enumerate(
            (("Ada Author", "author"), ("Unknown Credit", None))
        )
    )
    local_result = _result(
        evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=claims,
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.INELIGIBLE == local_result.outcome
    assert () == local_result.alternatives


@pytest.mark.parametrize(
    "value",
    (
        "Unknown",
        "UNKNOWN",
        "UnKnOwN",
        "Ｕｎｋｎｏｗｎ",
    ),
    ids=("plain", "upper", "mixed_case", "fullwidth"),
)
def test_controlled_package_placeholder_never_becomes_an_author_candidate(
    value: str,
) -> None:
    authors = TargetField(
        TargetDestination.AUTHORS,
        TargetFieldState.MISSING,
    )
    claim = _local_creator(value)
    report = _report(
        fields=_fields(None, authors=authors),
        local_claims=(claim,),
    )
    proposal_set = evaluate_report(report)
    result = _result(proposal_set, TargetDestination.AUTHORS)
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives
    assert ("no_supported_evidence",) == result.reasons
    assert claim == report.local_claims[0]
    assert report == MetadataReport.from_json(report.to_json())
    assert proposal_set == ProposalSet.from_json(proposal_set.to_json())
    assert proposal_set == evaluate_report(report)


@pytest.mark.parametrize(
    ("state", "current", "outcome", "reason"),
    (
        (
            TargetFieldState.MISSING,
            None,
            ProposalOutcome.INELIGIBLE,
            "no_supported_evidence",
        ),
        (
            TargetFieldState.NULL,
            None,
            ProposalOutcome.INELIGIBLE,
            "no_supported_evidence",
        ),
        (
            TargetFieldState.EMPTY,
            None,
            ProposalOutcome.INELIGIBLE,
            "no_supported_evidence",
        ),
        (
            TargetFieldState.UNLOADED,
            None,
            ProposalOutcome.INELIGIBLE,
            "target_state_unusable",
        ),
        (
            TargetFieldState.VALUE,
            ("Current Author",),
            ProposalOutcome.DEFER,
            "no_positive_preservation_evidence",
        ),
    ),
    ids=("missing", "null", "empty", "unloaded", "value"),
)
def test_controlled_placeholder_uses_existing_nonmutating_target_outcome(
    state: TargetFieldState,
    current: tuple[str, ...] | None,
    outcome: ProposalOutcome,
    reason: str,
) -> None:
    claim = _local_creator("Unknown")
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        state,
                        current,
                    ),
                ),
                local_claims=(claim,),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert outcome == result.outcome
    assert () == result.alternatives
    assert (reason,) == result.reasons


@pytest.mark.parametrize(
    ("claims", "expected"),
    (
        (
            (
                _local_creator("Unknown", occurrence=0),
                _local_creator("Ada Author", occurrence=1),
                _local_creator("Bea Writer", occurrence=2),
            ),
            ("Ada Author", "Bea Writer"),
        ),
        (
            (
                _local_creator("Ada Author", occurrence=0),
                _local_creator("UNKNOWN", occurrence=1),
                _local_creator("Bea Writer", occurrence=2),
            ),
            ("Ada Author", "Bea Writer"),
        ),
    ),
    ids=("leading_placeholder", "interior_placeholder"),
)
def test_mixed_package_creators_keep_real_author_order(
    claims: tuple[LocalClaim, ...],
    expected: tuple[str, ...],
) -> None:
    authors = TargetField(
        TargetDestination.AUTHORS,
        TargetFieldState.MISSING,
    )
    report = _report(
        fields=_fields(None, authors=authors),
        local_claims=claims,
    )
    result = _result(
        evaluate_report(report),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert expected == result.alternatives[0].value.value
    assert (
        tuple(
            sorted(
                claim.reference
                for claim in claims
                if claim.observed.strip().casefold() != "unknown"
            )
        )
        == result.alternatives[0].evidence_references
    )
    assert claims == report.local_claims


@pytest.mark.parametrize(
    "value",
    ("Anonymous", "Anon.", "Unknown Author", "The Unknown"),
    ids=("anonymous", "abbreviated", "unknown_author", "the_unknown"),
)
def test_placeholder_rule_is_exact_and_creator_context_specific(value: str) -> None:
    authors = TargetField(
        TargetDestination.AUTHORS,
        TargetFieldState.MISSING,
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(None, authors=authors),
                local_claims=(_local_creator(value),),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert (value,) == result.alternatives[0].value.value

    opening_unknown = _result(
        evaluate_report(
            _report(
                fields=_fields(None, authors=authors),
                local_claims=(_local_creator("Unknown", basis="opening_matter"),),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == opening_unknown.outcome
    assert ("Unknown",) == opening_unknown.alternatives[0].value.value

    provider_unknown = _result(
        evaluate_report(
            _report(
                fields=_fields(None, authors=authors),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.CREATOR,
                                "Unknown",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                        ),
                    ),
                ),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == provider_unknown.outcome
    assert ("Unknown",) == provider_unknown.alternatives[0].value.value

    mixed_context = _result(
        evaluate_report(
            _report(
                fields=_fields(None, authors=authors),
                local_claims=(
                    _local_creator("Unknown", occurrence=0),
                    _local_creator(
                        "Real Author",
                        basis="opening_matter",
                        occurrence=1,
                    ),
                ),
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == mixed_context.outcome
    assert ("Real Author",) == mixed_context.alternatives[0].value.value

    unrelated_report = _report(
        title=None,
        fields=_fields(
            None,
            title=TargetField(
                TargetDestination.TITLE,
                TargetFieldState.MISSING,
            ),
            authors=authors,
        ),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Unknown",
                canonical="Unknown",
                excerpt="Unknown",
                applicability="not_applicable",
                basis="package_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        ),
    )
    unrelated = evaluate_report(unrelated_report)
    assert (
        ProposalOutcome.PROPOSE_CHANGE
        == _result(unrelated, TargetDestination.TITLE).outcome
    )
    assert (
        ProposalOutcome.INELIGIBLE
        == _result(unrelated, TargetDestination.AUTHORS).outcome
    )


def test_placeholder_changes_only_the_authors_policy_result() -> None:
    authors = TargetField(
        TargetDestination.AUTHORS,
        TargetFieldState.MISSING,
    )
    baseline = evaluate_report(_report(fields=_fields(None, authors=authors)))
    with_placeholder = evaluate_report(
        _report(
            fields=_fields(None, authors=authors),
            local_claims=(_local_creator("Unknown"),),
        )
    )
    assert {
        result.destination: result
        for result in baseline.results
        if result.destination is not TargetDestination.AUTHORS
    } == {
        result.destination: result
        for result in with_placeholder.results
        if result.destination is not TargetDestination.AUTHORS
    }


def test_ambiguous_local_contributor_does_not_become_partial_narrators() -> None:
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.CONTRIBUTOR,
            observed=name,
            canonical=name,
            excerpt=name,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
            role=role,
            occurrence=index,
        )
        for index, (name, role) in enumerate(
            (("Nora Narrator", "narrator"), ("Unknown Credit", None))
        )
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    narrators=TargetField(
                        TargetDestination.NARRATORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=claims,
            )
        ),
        TargetDestination.NARRATORS,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_ambiguous_provider_contributor_does_not_become_partial_narrators() -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CONTRIBUTOR,
            "Nora Narrator",
            scope=ClaimScope.EDITION,
            role="narrator",
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CONTRIBUTOR,
            "Unknown Credit",
            scope=ClaimScope.EDITION,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    narrators=TargetField(
                        TargetDestination.NARRATORS,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.NARRATORS,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def _narrator(provider: str, candidate: str) -> ProviderClaim:
    return _claim(
        provider,
        candidate,
        ClaimKind.CONTRIBUTOR,
        "Nia Narrator",
        scope=ClaimScope.WORK,
        role="narrator",
    )


def test_a_narrator_cast_one_source_states_is_held_for_the_owner() -> None:
    proposal_set = evaluate_report(
        _report(
            fields=_fields(),
            providers=(
                _provider(
                    "hardcover",
                    "edition-1",
                    (_narrator("hardcover", "edition-1"),),
                    authority="hardcover",
                ),
            ),
        )
    )
    result = _result(proposal_set, TargetDestination.NARRATORS)
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("insufficient_independent_corroboration",) == result.reasons


def test_two_sources_stating_one_narrator_cast_leave_the_guard_silent() -> None:
    proposal_set = evaluate_report(
        _report(
            fields=_fields(),
            providers=(
                _provider(
                    "hardcover",
                    "edition-1",
                    (_narrator("hardcover", "edition-1"),),
                    authority="hardcover",
                ),
                _provider(
                    "open-library",
                    "edition-2",
                    (_narrator("open-library", "edition-2"),),
                    authority="open_library",
                ),
            ),
        )
    )
    result = _result(proposal_set, TargetDestination.NARRATORS)
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert ("unique_typed_candidate",) == result.reasons


@pytest.mark.parametrize("names", (("Ada Author",), ("Ada Author", "Bea Writer")))
@pytest.mark.parametrize(
    "witness", ("none", "opening", "provider", "partial", "conflict")
)
def test_neutral_package_creator_set_needs_complete_typed_corroboration(
    names: tuple[str, ...], witness: str
) -> None:
    neutral = tuple(
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed=name,
            canonical=name,
            excerpt=name,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            basis="package_metadata",
            scope=ClaimScope.WORK,
            role=None,
            occurrence=index,
        )
        for index, name in enumerate(names)
    )
    witness_names = (
        names
        if witness not in {"partial", "conflict"}
        else ("Other Author",)
        if witness == "conflict"
        else names[:-1]
    )
    opening = (
        tuple(
            _local_creator(name, basis="opening_matter", occurrence=index)
            for index, name in enumerate(witness_names)
        )
        if witness == "opening"
        else ()
    )
    providers = (
        (
            _provider(
                "catalogue",
                "edition-1",
                tuple(
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.CREATOR,
                        name,
                        scope=ClaimScope.WORK,
                        role="author",
                        order=index,
                    )
                    for index, name in enumerate(witness_names)
                ),
            ),
        )
        if witness in {"provider", "partial", "conflict"} and witness_names
        else ()
    )
    report = _report(
        fields=_fields(
            None,
            authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
        ),
        local_claims=neutral + opening,
        providers=providers,
    )
    result = _result(evaluate_report(report), TargetDestination.AUTHORS)
    references = {
        reference
        for alternative in result.alternatives
        for reference in alternative.evidence_references
    }
    assert {claim.reference for claim in neutral}.issubset(references) == (
        witness in {"opening", "provider"}
    )
    assert all(claim.role is None for claim in report.local_claims[: len(neutral)])
    if witness == "opening":
        assert len(result.alternatives) == 1
        assert len(result.alternatives[0].correlations) == 1
    if witness == "provider":
        assert len(result.alternatives[0].correlations) == 2
    if witness == "none":
        assert result.outcome is ProposalOutcome.INELIGIBLE


@pytest.mark.parametrize("current", (None, ("Ada Author",), ("Other Author",)))
def test_explicit_local_authors_only_fill_or_preserve(
    current: tuple[str, ...] | None,
) -> None:
    report = _report(
        fields=_fields(
            None,
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.MISSING if current is None else TargetFieldState.VALUE,
                current,
            ),
        ),
        local_claims=(_local_creator("Ada Author", basis="opening_matter"),),
    )
    result = _result(evaluate_report(report), TargetDestination.AUTHORS)
    assert result.outcome is (
        ProposalOutcome.PROPOSE_CHANGE
        if current is None
        else ProposalOutcome.KEEP
        if current == ("Ada Author",)
        else ProposalOutcome.UNRESOLVED_CHOICE
    )


@pytest.mark.parametrize("count", (2, 3))
@pytest.mark.parametrize("same_authority", (False, True))
def test_complete_author_replacement_counts_independent_authorities(
    count: int, same_authority: bool
) -> None:
    providers = tuple(
        _provider(
            name,
            name + "-edition",
            tuple(
                _claim(
                    name,
                    name + "-edition",
                    ClaimKind.CREATOR,
                    author,
                    scope=ClaimScope.WORK,
                    role="author",
                    order=index,
                )
                for index, author in enumerate(("Ada Author", "Bea Writer"))
            ),
            authority="hardcover" if same_authority else name,
        )
        for name in ("hardcover", "open_library", "google_books")[:count]
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.VALUE,
                        ("Current Writer",),
                    ),
                ),
                providers=providers,
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert result.outcome is (
        ProposalOutcome.UNRESOLVED_CHOICE
        if same_authority
        else ProposalOutcome.PROPOSE_CHANGE
    )
    assert len(result.alternatives[0].correlations) == (1 if same_authority else count)
    assert result.alternatives[0].value.value == ("Ada Author", "Bea Writer")


@pytest.mark.parametrize(
    "current,candidate,reordered",
    (
        (("Ada Author", "Bea Writer"), ("Bea Writer", "Ada Author"), True),
        (["ada author", "Bea  Writer"], ("Bea Writer", "Ada Author"), True),
        (("Ada Author", "Bea Writer"), ("Ada Author", "Bea Writer"), False),
        (("Ada Author",), ("Bea Writer", "Ada Author"), False),
        (("Ada Author", "Ada Author"), ("Ada Author",), False),
        (("Ada Author", "Bea Writer"), ("Bea Writer", "Ada Auther"), False),
        ("Ada Author", ("Ada Author",), False),
        ((), (), False),
    ),
)
def test_creator_list_reordered_is_the_same_multiset_in_another_order(
    current: object, candidate: object, reordered: bool
) -> None:
    assert creator_list_reordered(current, candidate) is reordered


def test_an_ebook_author_reorder_is_still_a_replacement() -> None:
    providers = tuple(
        _provider(
            name,
            name + "-edition",
            tuple(
                _claim(
                    name,
                    name + "-edition",
                    ClaimKind.CREATOR,
                    author,
                    scope=ClaimScope.WORK,
                    role="author",
                    order=index,
                )
                for index, author in enumerate(("Ada Author", "Bea Writer"))
            ),
            authority=name,
        )
        for name in ("hardcover", "open_library")
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.VALUE,
                        ("Bea Writer", "Ada Author"),
                    ),
                ),
                providers=providers,
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert result.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert [value.assessment for value in result.alternatives] == [
        ProposalAssessment.REPLACES_TARGET
    ]


@pytest.mark.parametrize(
    "target,assessment,outcome",
    (
        (
            ("B Writer", "A Writer"),
            ProposalAssessment.PRESERVES_TARGET,
            ProposalOutcome.KEEP,
        ),
        (("A Writer",), ProposalAssessment.REPLACES_TARGET, None),
    ),
)
def test_a_recordings_author_reorder_preserves_its_target(
    target: tuple[str, ...],
    assessment: ProposalAssessment,
    outcome: ProposalOutcome | None,
) -> None:
    from tests.support.audiobook_reports import (
        synthetic_audiobook_bibliographic_report,
    )

    report = synthetic_audiobook_bibliographic_report(
        source_authors=("A Writer", "B Writer"), target_authors=target
    )
    assert report.audio is not None
    result = _result(evaluate_report(report), TargetDestination.AUTHORS)
    assert [value.assessment for value in result.alternatives] == [assessment]
    assert outcome is None or result.outcome is outcome
    # The preserving alternative states the target's own order.
    assert outcome is None or result.alternatives[0].value.value == target


@pytest.mark.parametrize(
    "current,candidate,stripped",
    (
        ("Seán Example", "Sean Example", True),
        ("Seán Example", "SEAN EXAMPLE", True),
        ("Séan Example", "Seán Example", True),
        ("Zoë Sample", "Zoe Sample", True),
        ("Sean Example", "Seán Example", False),
        ("Seán Example", "Seán Example", False),
        ("Seán Example", "Shaun Example", False),
        ("Seán Example", "Sean Examples", False),
        ("Sean Example", "Sean Example", False),
    ),
)
def test_a_mark_stripped_member_drops_a_current_mark_from_the_same_letters(
    current: str, candidate: str, stripped: bool
) -> None:
    assert proposal_set._mark_stripped_member(current, candidate) is stripped


@pytest.mark.parametrize(
    "current,candidate,kept",
    (
        (("Seán Example",), ("Sean Example",), ("Seán Example",)),
        (
            ("Seán Example",),
            ("Sean Example", "New Reader"),
            ("Seán Example", "New Reader"),
        ),
        (
            ("Zoë Sample", "Seán Example"),
            ("Sean Example", "Zoe Sample"),
            ("Seán Example", "Zoë Sample"),
        ),
        # The first equal-base member pairs; the second is left as sourced.
        (
            ("Seán Example",),
            ("Seán Example", "Sean Example"),
            ("Seán Example", "Sean Example"),
        ),
        (("Sean Example",), ("Seán Example",), ("Seán Example",)),
        (("Seán Example",), ("Shaun Example",), ("Shaun Example",)),
    ),
    ids=("strip", "membership", "reorder", "paired_once", "adds_marks", "other"),
)
def test_incumbent_spellings_pair_each_current_member_at_most_once(
    current: tuple[str, ...], candidate: tuple[str, ...], kept: tuple[str, ...]
) -> None:
    assert proposal_set._incumbent_spellings(current, candidate) == kept


@pytest.mark.parametrize(
    "target,sourced,assessment,value",
    (
        (
            ("Seán Example",),
            ("Sean Example",),
            ProposalAssessment.PRESERVES_TARGET,
            ("Seán Example",),
        ),
        (
            ("Seán Example",),
            ("Sean Example", "New Writer"),
            ProposalAssessment.REPLACES_TARGET,
            ("Seán Example", "New Writer"),
        ),
        (
            ("Sean Example",),
            ("Seán Example",),
            ProposalAssessment.REPLACES_TARGET,
            ("Seán Example",),
        ),
    ),
    ids=("mark_strip", "membership_change", "adds_marks"),
)
def test_an_ebook_mark_stripped_author_keeps_the_incumbent_spelling(
    target: tuple[str, ...],
    sourced: tuple[str, ...],
    assessment: ProposalAssessment,
    value: tuple[str, ...],
) -> None:
    providers = tuple(
        _provider(
            name,
            name + "-edition",
            tuple(
                _claim(
                    name,
                    name + "-edition",
                    ClaimKind.CREATOR,
                    author,
                    scope=ClaimScope.WORK,
                    role="author",
                    order=index,
                )
                for index, author in enumerate(sourced)
            ),
            authority=name,
        )
        for name in ("hardcover", "open_library")
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS, TargetFieldState.VALUE, target
                    ),
                ),
                providers=providers,
            )
        ),
        TargetDestination.AUTHORS,
    )
    assert [(alt.assessment, alt.value.value) for alt in result.alternatives] == [
        (assessment, value)
    ]
    if assessment is ProposalAssessment.PRESERVES_TARGET:
        assert result.outcome is ProposalOutcome.KEEP
