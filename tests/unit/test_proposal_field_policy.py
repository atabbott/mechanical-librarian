"""Proposal field policy: subtitle scope admission, atomic series,
complete safe cover artifacts, and the eligibility rules that
refuse to manufacture evidence.
"""

import copy
import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    _candidate as _audible_candidate,
)
from mechanical_librarian.adapters.providers.audnexus_metadata import (
    _candidate as _audnexus_candidate,
)
from mechanical_librarian.adapters.providers.itunes_search_metadata import (
    _candidate as _itunes_candidate,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    IdentifierRelation,
    LocalClaim,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    comparison_links,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAssessment,
    ProposalOutcome,
    ProposalScope,
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)
from tests.support.audiobook_providers import payload
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    audio_local_claims,
    manual_audiobook_observation,
)
from tests.support.proposal_evidence import (
    _claim,
    _exact_edition_cover,
    _explicit_local_ebook_isbn,
    _fields,
    _local_isbn,
    _local_subtitle,
    _provider,
    _report,
    _result,
)


def _audible_html_description_candidate(
    local_claims: tuple[LocalClaim, ...],
) -> ProviderCandidate:
    document = copy.deepcopy(payload("audible_lookup"))["product"]
    del document["short_description"]
    return _audible_candidate(
        "audible",
        document,
        local_claims,
        marketplace="us",
        schema_rejections=lambda _count: None,
    )


def _audnexus_html_description_candidate(
    local_claims: tuple[LocalClaim, ...],
) -> ProviderCandidate:
    document = copy.deepcopy(payload("audnexus_book"))
    del document["description"]
    return _audnexus_candidate(
        "audnexus",
        document,
        local_claims,
        marketplace="us",
        chapters=None,
        schema_rejections=lambda _count: None,
    )


def _itunes_html_description_candidate(
    local_claims: tuple[LocalClaim, ...],
) -> ProviderCandidate:
    result = next(
        value
        for value in payload("itunes_search")["results"]
        if value["collectionName"] == "Ember Tide Protocol"
    )
    return _itunes_candidate(
        "itunes", result, local_claims, schema_rejections=lambda _count: None
    )


def test_populated_subjective_fields_do_not_get_replaced() -> None:
    genres = TargetField(
        TargetDestination.GENRES,
        TargetFieldState.VALUE,
        ("Fantasy",),
    )
    claim = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.GENRE,
        "Mystery",
        scope=ClaimScope.WORK,
    )
    proposal_set = evaluate_report(
        _report(
            fields=_fields(genres=genres),
            providers=(_provider("catalogue", "edition-1", (claim,)),),
        )
    )
    assert (
        ProposalOutcome.UNRESOLVED_CHOICE
        == _result(proposal_set, TargetDestination.GENRES).outcome
    )


@pytest.mark.parametrize(
    "name", ("hardcover", "open_library"), ids=("hardcover", "open_library")
)
def test_local_work_subtitle_and_provider_edition_subtitle_are_admitted(
    name: str,
) -> None:
    local = _local_subtitle("Local work subtitle", ClaimScope.WORK)
    local_result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(local,),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert {"Local work subtitle"} == {
        str(value.value.value) for value in local_result.alternatives
    }
    assert {ProposalScope.WORK} == {value.scope for value in local_result.alternatives}
    provider = _provider(
        name,
        "edition-1",
        (
            _claim(
                name,
                "edition-1",
                ClaimKind.SUBTITLE,
                "Edition subtitle",
                scope=ClaimScope.EDITION,
                applicability="ebook",
            ),
        ),
    )
    provider_result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(provider,),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert {"Edition subtitle"} == {
        str(value.value.value) for value in provider_result.alternatives
    }
    assert {ProposalScope.EDITION} == {
        value.scope for value in provider_result.alternatives
    }


def test_subtitle_scope_admission_is_source_sensitive() -> None:
    local_edition = _local_subtitle("Wrong local edition subtitle", ClaimScope.EDITION)
    provider_work = _provider(
        "open_library",
        "edition-1",
        (
            _claim(
                "open_library",
                "edition-1",
                ClaimKind.SUBTITLE,
                "Wrong provider work subtitle",
                scope=ClaimScope.WORK,
            ),
        ),
    )
    local_result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE,
                        TargetFieldState.MISSING,
                    )
                ),
                local_claims=(local_edition,),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert ProposalOutcome.INELIGIBLE == local_result.outcome
    assert () == local_result.alternatives
    provider_result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE,
                        TargetFieldState.MISSING,
                    )
                ),
                providers=(provider_work,),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert ProposalOutcome.INELIGIBLE == provider_result.outcome
    assert () == provider_result.alternatives


def test_generic_provider_subtitle_preserves_blank_without_making_a_case() -> None:
    generic = _provider(
        "catalogue",
        "edition-1",
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.TITLE,
                "Current title",
                scope=ClaimScope.WORK,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SUBTITLE,
                "A Novel",
                scope=ClaimScope.EDITION,
                applicability="ebook",
                order=1,
            ),
        ),
    )

    proposal = evaluate_report(
        _report(
            fields=_fields(
                subtitle=TargetField(
                    TargetDestination.SUBTITLE, TargetFieldState.MISSING
                )
            ),
            providers=(generic,),
        )
    )

    subtitle = _result(proposal, TargetDestination.SUBTITLE)
    title = _result(proposal, TargetDestination.TITLE)
    assert (ProposalOutcome.INELIGIBLE, ("generic_provider_subtitle",)) == (
        subtitle.outcome,
        subtitle.reasons,
    )
    assert () == subtitle.alternatives
    assert (ProposalOutcome.KEEP, ("positive_preservation_evidence",)) == (
        title.outcome,
        title.reasons,
    )
    assert title.alternatives[0].assessment is ProposalAssessment.PRESERVES_TARGET
    assert 1 == len(proposal.bibliographic_combinations)
    assert all(
        selection.kind.value == "preserve_target"
        for selection in proposal.bibliographic_combinations[0].selections
    )
    assert 1 == len(proposal.bibliographic_combinations[0].source_representations)

    tampered = json.loads(proposal.to_json())
    tampered["relationships"]["bibliographic_combinations"][0][
        "source_representations"
    ] = []
    with pytest.raises(ProposalValueError):
        ProposalSet.from_json(json.dumps(tampered))


def test_generic_provider_subtitle_retains_evidence_for_a_populated_target() -> None:
    proposal = evaluate_report(
        _report(
            fields=_fields(
                subtitle=TargetField(
                    TargetDestination.SUBTITLE, TargetFieldState.VALUE, "A Novel"
                )
            ),
            providers=(
                _provider(
                    "catalogue",
                    "edition-1",
                    (
                        _claim(
                            "catalogue",
                            "edition-1",
                            ClaimKind.SUBTITLE,
                            "A Novel",
                            scope=ClaimScope.EDITION,
                            applicability="ebook",
                        ),
                    ),
                ),
            ),
        )
    )

    subtitle = _result(proposal, TargetDestination.SUBTITLE)
    assert ProposalOutcome.KEEP is subtitle.outcome
    assert "generic_provider_subtitle" not in subtitle.reasons
    assert subtitle.alternatives


@pytest.mark.parametrize(
    "value",
    ("A Novel of Manners", "Novel", "A Novella"),
)
def test_generic_provider_subtitle_vocabulary_does_not_discard_legitimate_text(
    value: str,
) -> None:
    provider = _provider(
        "catalogue",
        "edition-1",
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SUBTITLE,
                value,
                scope=ClaimScope.EDITION,
                applicability="ebook",
            ),
        ),
    )

    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE, TargetFieldState.MISSING
                    )
                ),
                providers=(provider,),
            )
        ),
        TargetDestination.SUBTITLE,
    )

    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert {value} == {candidate.value.value for candidate in result.alternatives}


def test_generic_subtitle_rule_does_not_apply_to_local_evidence() -> None:
    local = _local_subtitle("A Novel", ClaimScope.WORK)
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE, TargetFieldState.MISSING
                    )
                ),
                local_claims=(local,),
            )
        ),
        TargetDestination.SUBTITLE,
    )

    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome


def test_equal_subtitles_keep_local_and_provider_scopes_separate() -> None:
    local = _local_subtitle("Shared subtitle", ClaimScope.WORK)
    provisional = _claim(
        "hardcover",
        "edition-1",
        ClaimKind.SUBTITLE,
        "Shared subtitle",
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    provider_claim = replace(
        provisional,
        links=comparison_links(provisional, (local,)),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE,
                        TargetFieldState.MISSING,
                    )
                ),
                local_claims=(local,),
                providers=(
                    _provider(
                        "hardcover",
                        "edition-1",
                        (provider_claim,),
                        authority="hardcover",
                    ),
                ),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert ("local_attested_subtitle",) == result.reasons
    assert 1 == len(result.alternatives)
    assert result.selected_alternative_id == result.alternatives[0].alternative_id
    assert "Shared subtitle" == result.alternatives[0].value.value
    assert 1 == len(result.alternatives[0].correlations)


def _empty_subtitle_report(
    local_subtitles: tuple[str, ...],
    provider_subtitles: tuple[str, ...],
    target_state: TargetFieldState = TargetFieldState.MISSING,
):
    locals_ = tuple(
        _local_subtitle(value, ClaimScope.WORK) for value in local_subtitles
    )
    candidates = tuple(
        ProviderCandidate(
            f"edition-{index + 1}",
            "hardcover-work",
            (
                _claim(
                    "hardcover",
                    f"edition-{index + 1}",
                    ClaimKind.SUBTITLE,
                    value,
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                ),
            ),
        )
        for index, value in enumerate(provider_subtitles)
    )
    subtitle = (
        TargetField(TargetDestination.SUBTITLE, target_state, "Existing subtitle")
        if target_state is TargetFieldState.VALUE
        else TargetField(TargetDestination.SUBTITLE, target_state)
    )
    return _report(
        fields=_fields(subtitle=subtitle),
        local_claims=locals_,
        providers=(
            ProviderObservation(
                "hardcover",
                ProviderStatus.CANDIDATES,
                (),
                candidates,
                authority="hardcover",
            ),
        ),
    )


def test_attested_subtitle_resolves_competing_empty_fill() -> None:
    result = _result(
        evaluate_report(
            _empty_subtitle_report(
                ("Printed subtitle",),
                ("Printed subtitle", "Catalogue artifact"),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert ("local_attested_subtitle",) == result.reasons
    assert "Printed subtitle" == result.alternatives[0].value.value
    assert ProposalScope.EDITION is result.alternatives[0].scope


def test_local_only_attestation_never_elects_a_subtitle() -> None:
    report = _report(
        fields=_fields(
            subtitle=TargetField(TargetDestination.SUBTITLE, TargetFieldState.MISSING)
        ),
        local_claims=(_local_subtitle("Printed subtitle", ClaimScope.WORK),),
    )
    result = _result(evaluate_report(report), TargetDestination.SUBTITLE)
    assert ("local_attested_subtitle",) != result.reasons
    assert all(
        ProposalScope.EDITION is not alternative.scope
        for alternative in result.alternatives
    )


def test_unattested_competing_subtitles_stay_an_owner_decision() -> None:
    result = _result(
        evaluate_report(
            _empty_subtitle_report(
                (),
                ("One catalogue split", "Another catalogue split"),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert ("competing_evidence",) == result.reasons


def test_conflicting_attested_subtitles_stay_an_owner_decision() -> None:
    result = _result(
        evaluate_report(
            _empty_subtitle_report(
                ("First printed form", "Second printed form"),
                ("First printed form", "Second printed form"),
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert ("competing_evidence",) == result.reasons


def test_populated_subtitle_never_uses_local_attestation_election() -> None:
    result = _result(
        evaluate_report(
            _empty_subtitle_report(
                ("Printed subtitle",),
                ("Printed subtitle", "Catalogue artifact"),
                target_state=TargetFieldState.VALUE,
            )
        ),
        TargetDestination.SUBTITLE,
    )
    assert ProposalOutcome.PROPOSE_CHANGE is not result.outcome or (
        result.reasons != ("local_attested_subtitle",)
    )


def test_candidates_from_one_provider_are_one_correlation_group() -> None:
    candidates = tuple(
        ProviderCandidate(
            identity,
            "catalogue-work",
            (
                _claim(
                    "catalogue",
                    identity,
                    ClaimKind.TITLE,
                    "Corrected title",
                    scope=ClaimScope.WORK,
                ),
            ),
        )
        for identity in ("edition-1", "edition-2")
    )
    provider = ProviderObservation(
        "catalogue",
        ProviderStatus.CANDIDATES,
        (),
        candidates,
    )
    result = _result(
        evaluate_report(_report(providers=(provider,))),
        TargetDestination.TITLE,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert 1 == len(result.alternatives[0].correlations)
    assert "insufficient_independent_corroboration" in result.reasons


def test_partial_multiple_series_never_invents_pairing() -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Reading order",
            scope=ClaimScope.WORK,
            order=0,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Publication order",
            scope=ClaimScope.WORK,
            order=1,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES_SEQUENCE,
            "2",
            scope=ClaimScope.WORK,
            order=2,
        ),
    )
    result = _result(
        evaluate_report(
            _report(providers=(_provider("catalogue", "edition-1", claims),))
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_complete_multiple_series_preserves_exact_names_and_sequences() -> None:
    def claims(provider: str) -> tuple[ProviderClaim, ...]:
        return (
            _claim(
                provider,
                "edition-1",
                ClaimKind.SERIES,
                "Reading Order",
                scope=ClaimScope.WORK,
                order=0,
            ),
            _claim(
                provider,
                "edition-1",
                ClaimKind.SERIES_SEQUENCE,
                "2",
                scope=ClaimScope.WORK,
                order=1,
            ),
            _claim(
                provider,
                "edition-1",
                ClaimKind.SERIES,
                "Publication Order",
                scope=ClaimScope.WORK,
                order=2,
            ),
            ProviderClaim.create(
                provider=provider,
                candidate="edition-1",
                kind=ClaimKind.SERIES_SEQUENCE,
                value=None,
                state=FieldState.ABSENT,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                raw_order=3,
            ),
        )

    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(
                    _provider("catalogue", "edition-1", claims("catalogue")),
                    _provider(
                        "open_library",
                        "edition-1",
                        claims("open_library"),
                    ),
                ),
            )
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome, result.to_data()
    assert (
        TargetSeries("Reading Order", "2"),
        TargetSeries("Publication Order", None),
    ) == result.alternatives[0].value.value


@pytest.mark.parametrize(
    "state",
    (FieldState.MALFORMED, FieldState.OVERSIZED),
    ids=("malformed", "oversized"),
)
def test_unusable_series_sequence_withholds_the_source_aggregate(
    state: FieldState,
) -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Reading order",
            scope=ClaimScope.WORK,
        ),
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.SERIES_SEQUENCE,
            value=None,
            state=state,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            raw_order=1,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    series=TargetField(
                        TargetDestination.SERIES,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_series_preservation_normalizes_names_but_not_sequences() -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Reading Order",
            scope=ClaimScope.WORK,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES_SEQUENCE,
            "2",
            scope=ClaimScope.WORK,
            order=1,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES,
                        TargetFieldState.VALUE,
                        (TargetSeries("reading   order", "2"),),
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.KEEP == result.outcome


def test_series_preservation_uses_closed_display_equivalence() -> None:
    claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "The Reading Order Trilogy",
            scope=ClaimScope.WORK,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES_SEQUENCE,
            "2",
            scope=ClaimScope.WORK,
            order=1,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES,
                        TargetFieldState.VALUE,
                        (TargetSeries("Reading Order", "2"),),
                    ),
                ),
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.SERIES,
    )

    assert ProposalOutcome.KEEP == result.outcome


def test_series_election_collapses_only_closed_display_variants() -> None:
    providers = (
        _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES,
                    "The Reading Order Trilogy",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES_SEQUENCE,
                    "2",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
        _provider(
            "search",
            "edition-2",
            (
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES,
                    "reading order",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES_SEQUENCE,
                    "2",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    )
                ),
                providers=providers,
            )
        ),
        TargetDestination.SERIES,
    )

    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert 1 == len(result.alternatives)


def test_series_election_does_not_fuzzily_collapse_substantive_names() -> None:
    providers = (
        _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES,
                    "The Reading Order Trilogy",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES_SEQUENCE,
                    "2",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
        _provider(
            "search",
            "edition-2",
            (
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES,
                    "Reading Order Companion",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES_SEQUENCE,
                    "2",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    )
                ),
                providers=providers,
            )
        ),
        TargetDestination.SERIES,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert 2 == len(result.alternatives)


def test_series_election_keeps_distinct_sequences_for_closed_name_variants() -> None:
    providers = (
        _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES,
                    "The Reading Order Trilogy",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES_SEQUENCE,
                    "2",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
        _provider(
            "search",
            "edition-2",
            (
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES,
                    "reading order",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES_SEQUENCE,
                    "3",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    )
                ),
                providers=providers,
            )
        ),
        TargetDestination.SERIES,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert 2 == len(result.alternatives)


def test_series_election_keeps_generic_decoration_names_distinct() -> None:
    providers = (
        _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES,
                    "Series",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.SERIES_SEQUENCE,
                    "1",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
        _provider(
            "search",
            "edition-2",
            (
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES,
                    "Trilogy",
                    scope=ClaimScope.WORK,
                ),
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES_SEQUENCE,
                    "1",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    )
                ),
                providers=providers,
            )
        ),
        TargetDestination.SERIES,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert 2 == len(result.alternatives)


def test_series_preservation_accepts_local_sequence_before_name() -> None:
    claims = (
        LocalClaim.create(
            kind=ClaimKind.SERIES_SEQUENCE,
            observed="2",
            canonical="2",
            excerpt="2",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            occurrence=0,
        ),
        LocalClaim.create(
            kind=ClaimKind.SERIES,
            observed="Reading Order",
            canonical="Reading Order",
            excerpt="Reading Order",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            occurrence=1,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(),
                local_claims=claims,
            )
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert (TargetSeries("Reading Order", "2"),) == result.alternatives[0].value.value


def test_series_without_sequence_remains_eligible() -> None:
    claims = (
        _claim(
            "open_library",
            "edition-1",
            ClaimKind.SERIES,
            "Reading Order",
            scope=ClaimScope.WORK,
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    series=TargetField(
                        TargetDestination.SERIES,
                        TargetFieldState.VALUE,
                        (TargetSeries("reading order", None),),
                    ),
                ),
                providers=(_provider("open_library", "edition-1", claims),),
            )
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.KEEP == result.outcome


@pytest.mark.parametrize(
    "kind", [CoverSourceKind.HARDCOVER, CoverSourceKind.INVENTAIRE]
)
def test_cover_requires_one_complete_safe_artifact(kind: CoverSourceKind) -> None:
    artifact = CoverArtifact.create(b"cover", b"preview", "image/png", 10, 20)
    source = CoverSource(
        cover_source_id(kind, "catalogue:cover"),
        kind,
        CoverSourceState.RETRIEVED,
        artifact,
        provenance=CoverProvenance(
            "catalogue",
            CoverDesignation.WORK,
            "catalogue-work",
        ),
    )
    proposal_set = evaluate_report(_report(covers=(source,)))
    result = _result(proposal_set, TargetDestination.COVER)
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert (source.source_id,) == result.alternatives[0].evidence_references
    assert artifact.artifact_id == result.alternatives[0].value.value
    assert proposal_set == ProposalSet.from_json(proposal_set.to_json())
    timeout = CoverSource(
        cover_source_id(kind, "catalogue:timeout"),
        kind,
        CoverSourceState.TIMEOUT,
        timeout_scope="attempted",
        provenance=CoverProvenance(
            "catalogue",
            CoverDesignation.WORK,
            "catalogue-work",
        ),
    )
    incomplete = _result(
        evaluate_report(_report(covers=(source, timeout))),
        TargetDestination.COVER,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == incomplete.outcome
    assert incomplete.alternatives == result.alternatives
    assert (
        "cover_gallery_incomplete",
        "owner_cover_choice_required",
    ) == incomplete.reasons


def test_verified_incumbent_and_alternative_require_owner_cover_choice() -> None:
    incumbent = CoverArtifact.create(
        b"incumbent", b"incumbent-preview", "image/png", 10, 20
    )
    alternative = CoverArtifact.create(
        b"alternative", b"alternative-preview", "image/png", 12, 24
    )
    sources = (
        CoverSource(
            cover_source_id(CoverSourceKind.ABS, "current"),
            CoverSourceKind.ABS,
            CoverSourceState.RETRIEVED,
            incumbent,
        ),
        CoverSource(
            cover_source_id(CoverSourceKind.EPUB, "embedded"),
            CoverSourceKind.EPUB,
            CoverSourceState.RETRIEVED,
            alternative,
        ),
    )
    fields = tuple(
        TargetField(
            value.destination,
            TargetFieldState.VALUE,
            incumbent.artifact_id,
        )
        if value.destination is TargetDestination.COVER
        else value
        for value in _fields("Current title")
    )
    result = _result(
        evaluate_report(_report(fields=fields, covers=sources)),
        TargetDestination.COVER,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("current_cover_verified", "owner_cover_choice_required") == result.reasons
    assert 2 == len(result.alternatives)


def test_populated_unverified_cover_is_manual_only_with_typed_incumbent_gap() -> None:
    artifact = CoverArtifact.create(b"cover", b"preview", "image/png", 10, 20)
    source = CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:cover"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        artifact,
        provenance=CoverProvenance(
            "catalogue",
            CoverDesignation.WORK,
            "catalogue-work",
        ),
    )
    fields = _fields(
        cover=TargetField(
            TargetDestination.COVER,
            TargetFieldState.VALUE,
            "current-cover",
        )
    )
    result = _result(
        evaluate_report(_report(fields=fields, covers=(source,))),
        TargetDestination.COVER,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert (
        "current_cover_unavailable",
        "current_cover_unverified",
        "owner_cover_choice_required",
    ) == result.reasons
    assert 1 == len(result.alternatives)
    assert result.selected_alternative_id is None


def test_non_google_markup_description_is_ineligible() -> None:
    claim = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "Summary <em>",
        scope=ClaimScope.WORK,
    )
    result = _result(
        evaluate_report(
            _report(providers=(_provider("catalogue", "edition-1", (claim,)),))
        ),
        TargetDestination.DESCRIPTION,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome


@pytest.mark.parametrize(
    ("provider", "authority", "candidate_factory"),
    (
        ("audible", "audible_catalog", _audible_html_description_candidate),
        ("audnexus", "audnexus", _audnexus_html_description_candidate),
        ("itunes", "itunes_search", _itunes_html_description_candidate),
    ),
)
def test_generic_html_visible_description_remains_a_manual_alternative(
    provider: str,
    authority: str,
    candidate_factory: Callable[[tuple[LocalClaim, ...]], ProviderCandidate],
) -> None:
    local_claims = audio_local_claims()
    candidate = candidate_factory(local_claims)
    observation = manual_audiobook_observation(provider, authority, candidate)
    report = admitted_audiobook_report(
        local_claims, additional_providers=(observation,)
    )
    result = _result(evaluate_report(report), TargetDestination.DESCRIPTION)
    description = next(
        claim for claim in candidate.claims if claim.kind is ClaimKind.DESCRIPTION
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert result.selected_alternative_id is not None
    assert len(result.alternatives) == 1
    alternative = result.alternatives[0]
    assert alternative.value.value == description.value
    assert ProposalScope.WORK is alternative.scope
    assert ProposalAssessment.FILLS_EMPTY is alternative.assessment
    assert (description.reference,) == alternative.evidence_references
    assert ("html-visible-text-v1",) == tuple(
        evidence.value
        for evidence in candidate.evidence
        if evidence.kind is ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION
    )


def test_transformed_google_visible_angle_characters_remain_eligible() -> None:
    claim = _claim(
        "google",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "2 < 3",
        scope=ClaimScope.WORK,
    )
    result = _result(
        evaluate_report(
            _report(
                providers=(
                    _provider(
                        "google",
                        "edition-1",
                        (claim,),
                        (
                            ProviderEvidence(
                                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                                "google-books-html-visible-text-v1",
                                ClaimScope.WORK,
                            ),
                        ),
                    ),
                )
            )
        ),
        TargetDestination.DESCRIPTION,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert "2 < 3" == result.alternatives[0].value.value


@pytest.mark.parametrize(
    "applicability",
    ("ebook", "print", "unknown", "ambiguous"),
    ids=("ebook", "print", "unknown", "ambiguous"),
)
@pytest.mark.parametrize(
    ("destination", "kind", "value"),
    (
        (TargetDestination.SUBTITLE, ClaimKind.SUBTITLE, "Edition subtitle"),
        (TargetDestination.ASIN, ClaimKind.ASIN, "B012345678"),
    ),
    ids=("subtitle", "asin"),
)
def test_provider_subtitle_and_asin_require_exact_ebook_applicability(
    destination: TargetDestination, kind: ClaimKind, value: str, applicability: str
) -> None:
    claim = _claim(
        "catalogue",
        "edition-1",
        kind,
        value,
        scope=ClaimScope.EDITION,
        applicability=applicability,
    )
    result = _result(
        evaluate_report(
            _report(
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (claim,),
                    ),
                )
            )
        ),
        destination,
    )
    assert (
        ProposalOutcome.PROPOSE_CHANGE
        if applicability == "ebook"
        else ProposalOutcome.INELIGIBLE
    ) == result.outcome


_MATCHING_DESCRIPTION = "A complete description " + "x" * 700
_DIFFERING_DESCRIPTION = "A different description " + "y" * 700


@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        (_MATCHING_DESCRIPTION, ProposalOutcome.KEEP),
        (_DIFFERING_DESCRIPTION, ProposalOutcome.UNRESOLVED_CHOICE),
    ),
    ids=("matching", "differing"),
)
def test_long_descriptions_compare_without_title_bounds(
    candidate: str, expected: ProposalOutcome
) -> None:
    field = TargetField(
        TargetDestination.DESCRIPTION,
        TargetFieldState.VALUE,
        _MATCHING_DESCRIPTION,
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(description=field),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.DESCRIPTION,
                                candidate,
                                scope=ClaimScope.WORK,
                            ),
                        ),
                    ),
                ),
            )
        ),
        TargetDestination.DESCRIPTION,
    )
    assert expected == result.outcome


def test_malformed_target_is_ineligible_even_with_evidence() -> None:
    claim = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.LANGUAGE,
        "en",
        scope=ClaimScope.EDITION,
    )
    fields = _fields(
        language=TargetField(
            TargetDestination.LANGUAGE,
            TargetFieldState.MALFORMED,
        )
    )
    result = _result(
        evaluate_report(
            _report(
                fields=fields,
                providers=(_provider("catalogue", "edition-1", (claim,)),),
            )
        ),
        TargetDestination.LANGUAGE,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome


def test_identifier_and_explicit_rules_do_not_manufacture_evidence() -> None:
    isbn = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.ISBN,
        "0306406152",
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    fields = _fields(
        isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING),
        explicit=TargetField(
            TargetDestination.EXPLICIT,
            TargetFieldState.VALUE,
            False,
        ),
    )
    report = _report(
        fields=fields,
        providers=(_provider("catalogue", "edition-1", (isbn,)),),
    )
    assert {IdentifierRelation.UNRESOLVED} == {
        evidence.relation for evidence in report.identifier_evidence
    }
    proposal_set = evaluate_report(report)
    isbn_result = _result(proposal_set, TargetDestination.ISBN)
    assert ProposalOutcome.DEFER == isbn_result.outcome
    assert not isbn_result.alternatives
    assert ("identifier_applicability_unresolved",) == isbn_result.reasons
    assert (
        ProposalOutcome.DEFER
        == _result(proposal_set, TargetDestination.EXPLICIT).outcome
    )


def test_invalid_isbn_lineage_is_specific_when_empty_target_has_no_alternative() -> (
    None
):
    invalid = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="not-an-isbn",
        canonical=None,
        excerpt="not-an-isbn",
        applicability="unknown",
        checksum="invalid",
        applicability_basis="unlabelled",
        scope=ClaimScope.EDITION,
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(invalid,),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives
    assert ("identifier_validity_invalid",) == result.reasons


@pytest.mark.parametrize(
    "state",
    (
        TargetFieldState.MALFORMED,
        TargetFieldState.UNLOADED,
        TargetFieldState.OVERSIZED,
    ),
)
def test_isbn_lineage_does_not_override_unusable_target_state(
    state: TargetFieldState,
) -> None:
    invalid = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="not-an-isbn",
        canonical=None,
        excerpt="not-an-isbn",
        applicability="unknown",
        checksum="invalid",
        applicability_basis="unlabelled",
        scope=ClaimScope.EDITION,
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(TargetDestination.ISBN, state),
                ),
                local_claims=(invalid,),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert ("target_state_unusable",) == result.reasons


def test_isbn_lineage_does_not_override_populated_target_preservation() -> None:
    invalid = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="not-an-isbn",
        canonical=None,
        excerpt="not-an-isbn",
        applicability="unknown",
        checksum="invalid",
        applicability_basis="unlabelled",
        scope=ClaimScope.EDITION,
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.VALUE,
                        "9780306406157",
                    ),
                ),
                local_claims=(invalid,),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.DEFER == result.outcome
    assert ("no_positive_preservation_evidence",) == result.reasons


def test_local_only_isbn_remains_owner_visible_without_election() -> None:
    isbn = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="0306406152",
        canonical="0306406152",
        excerpt="0306406152",
        applicability="ebook",
        checksum="valid_isbn_10",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(isbn,),
            )
        ),
        TargetDestination.ISBN,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("manifestation_verification_required",) == result.reasons
    assert "0306406152" == result.alternatives[0].value.value


def test_not_for_sale_prepublication_proof_keeps_exact_work_isbn_electable() -> None:
    proof_package_isbn = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
        basis="package_metadata",
    )
    provider = _provider(
        "catalogue",
        "edition-1",
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.ISBN,
                "0306406152",
                scope=ClaimScope.EDITION,
                applicability="ebook",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="ebook",
                order=1,
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(proof_package_isbn,),
                providers=(provider,),
                covers=(_exact_edition_cover(),),
            )
        ),
        TargetDestination.ISBN,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert ("verified_manifestation_isbn",) == result.reasons
    assert "package_metadata" == proof_package_isbn.basis
    assert 1 == len(result.alternatives)
    assert "9780306406157" == result.alternatives[0].value.value
    assert ProposalAssessment.FILLS_EMPTY == result.alternatives[0].assessment
    assert 3 == len(result.alternatives[0].evidence_references)


@pytest.mark.parametrize(
    "transport",
    (
        CoverSourceState.ABSENT,
        CoverSourceState.UNAVAILABLE,
        CoverSourceState.DECODE_FAILED,
    ),
)
def test_empty_isbn_election_retains_identity_without_image(
    transport: CoverSourceState,
) -> None:
    cover = _exact_edition_cover()
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
                applicability="ebook",
            ),
        ),
    )

    def result(source: CoverSource) -> object:
        return _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        isbn=TargetField(
                            TargetDestination.ISBN, TargetFieldState.MISSING
                        )
                    ),
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=(provider,),
                    covers=(source,),
                )
            ),
            TargetDestination.ISBN,
        )

    retrieved = result(cover)
    unavailable = result(replace(cover, state=transport, artifact=None))
    assert unavailable == retrieved
    assert (
        _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        isbn=TargetField(
                            TargetDestination.ISBN, TargetFieldState.MISSING
                        )
                    ),
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=(provider,),
                    covers=(),
                )
            ),
            TargetDestination.ISBN,
        ).selected_alternative_id
        is None
    )


def test_empty_isbn_rejects_an_unresolved_provider_work_identity() -> None:
    local = _local_isbn("0306406152", "valid_isbn_10")
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
                applicability="ebook",
            ),
        ),
    )
    unbound_provider = replace(
        provider,
        candidates=(replace(provider.candidates[0], work_identity="unresolved"),),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
                ),
                local_claims=(local,),
                providers=(unbound_provider,),
                covers=(_exact_edition_cover(work_identity="unresolved"),),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("manifestation_verification_required",) == result.reasons
    assert 1 == len(result.alternatives)


def test_empty_isbn_requires_retained_exact_edition_provenance() -> None:
    accepted = _explicit_local_ebook_isbn()
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
                applicability="ebook",
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
                ),
                local_claims=(accepted,),
                providers=(provider,),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("manifestation_verification_required",) == result.reasons


def test_empty_isbn_rejects_associated_only_provider_identifier() -> None:
    accepted = _explicit_local_ebook_isbn()
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
        ),
    )
    report = _report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(accepted,),
        providers=(provider,),
        covers=(_exact_edition_cover(),),
    )
    result = _result(evaluate_report(report), TargetDestination.ISBN)

    assert {IdentifierRelation.ASSOCIATED} == {
        evidence.relation
        for evidence in report.identifier_evidence
        if evidence.provider == "catalogue"
    }
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("manifestation_verification_required",) == result.reasons


@pytest.mark.parametrize(
    ("provider_name", "candidate", "work_identity"),
    (
        ("other", "edition-1", "catalogue-work"),
        ("catalogue", "edition-2", "catalogue-work"),
        ("catalogue", "edition-1", "other-work"),
    ),
)
def test_empty_isbn_requires_cover_provenance_bound_to_its_provider_candidate_work(
    provider_name: str,
    candidate: str,
    work_identity: str,
) -> None:
    accepted = _explicit_local_ebook_isbn()
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
                applicability="ebook",
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
                ),
                local_claims=(accepted,),
                providers=(provider,),
                covers=(_exact_edition_cover(provider_name, candidate, work_identity),),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("manifestation_verification_required",) == result.reasons


def test_empty_isbn_rejects_publication_incompatible_exact_cover() -> None:
    accepted = _explicit_local_ebook_isbn()
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
                applicability="ebook",
            ),
        ),
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.PHYSICAL_FORMAT,
                "hardcover",
                ClaimScope.EDITION,
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
                ),
                local_claims=(accepted,),
                providers=(provider,),
                covers=(_exact_edition_cover(),),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("manifestation_verification_required",) == result.reasons


@pytest.mark.parametrize(
    "state",
    (TargetFieldState.MISSING, TargetFieldState.NULL, TargetFieldState.EMPTY),
)
def test_empty_isbn_elects_only_the_family_with_its_own_accepted_anchor(
    state: TargetFieldState,
) -> None:
    accepted = _explicit_local_ebook_isbn()
    other_accepted = _local_isbn("9780140328721", "valid_isbn_13")
    supported = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.ISBN,
        "9780306406157",
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    unverified = _claim(
        "other",
        "edition-2",
        ClaimKind.ISBN,
        "9780140328721",
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(isbn=TargetField(TargetDestination.ISBN, state)),
                local_claims=(accepted, other_accepted),
                providers=(
                    _provider("catalogue", "edition-1", (supported,)),
                    _provider("other", "edition-2", (unverified,)),
                ),
                covers=(_exact_edition_cover(),),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert ("verified_manifestation_isbn",) == result.reasons
    assert 2 == len(result.alternatives)
    selected = next(
        alternative
        for alternative in result.alternatives
        if alternative.alternative_id == result.selected_alternative_id
    )
    assert "9780306406157" == selected.value.value


def test_multiple_accepted_isbn_families_remain_an_owner_choice() -> None:
    first = _explicit_local_ebook_isbn()
    second = _local_isbn("9780140328721", "valid_isbn_13")
    providers = (
        _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                ),
            ),
        ),
        _provider(
            "other",
            "edition-2",
            (
                _claim(
                    "other",
                    "edition-2",
                    ClaimKind.ISBN,
                    "9780140328721",
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                ),
            ),
        ),
    )
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(
                    isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
                ),
                local_claims=(first, second),
                providers=providers,
                covers=(
                    _exact_edition_cover(),
                    _exact_edition_cover("other", "edition-2", "other-work"),
                ),
            )
        ),
        TargetDestination.ISBN,
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("manifestation_verification_required",) == result.reasons
    assert 2 == len(result.alternatives)
