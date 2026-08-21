"""Direct contract proof for the no-effect rich metadata preview compiler."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
    provider_lane_candidate_reference,
)
from mechanical_librarian.domain.proposal_set import (
    BibliographicCombination,
    BibliographicSelection,
    ProposalAlternative,
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    ProposalScope,
    ProposalSet,
    ProposalValue,
    ProposalValueError,
    ProposalValueKind,
    evaluate_report,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    compile_rich_metadata_preview,
    project_f20_automatic_decision_value,
)
from tests.support.proposal_evidence import _claim, _fields, _provider, _report
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source


def _local_isbn(value, checksum):
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="ebook",
        checksum=checksum,
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def _preview_field(report, field, option):
    options = dict(MetadataPolicyProfile.builtin().options)
    options[field] = option
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations[field] = "ask"
    profile = MetadataPolicyProfile.create(
        "Exact field preview",
        options,
        destinations,
    )
    policy = resolve_policy(profile, report.target.library)
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    return next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == field
    )


def _automatic_series_preview(report, option="source_order"):
    options = dict(MetadataPolicyProfile.builtin().options)
    options["series"] = option
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["series"] = "automatic"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Automatic series", options, destinations),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    return next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "series"
    )


def _series_claims(provider, candidate, name="Reading Order", sequence="2"):
    return (
        _claim(
            provider,
            candidate,
            ClaimKind.SERIES,
            name,
            scope=ClaimScope.WORK,
        ),
        _claim(
            provider,
            candidate,
            ClaimKind.SERIES_SEQUENCE,
            sequence,
            scope=ClaimScope.WORK,
            order=1,
        ),
    )


def _automatic_series_report(*providers):
    return _report(
        fields=_fields(
            series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING)
        ),
        providers=providers,
    )


def _series_provider(name, candidate, authority, claims=None):
    return _provider(
        name,
        candidate,
        _series_claims(name, candidate) if claims is None else claims,
        authority=authority,
    )


def _with_state(report, destination, state):
    return MetadataReport.create(
        target=TargetFacts(
            report.target.connection,
            report.target.library,
            report.target.item,
            tuple(
                TargetField(field.destination, state)
                if field.destination is destination
                else field
                for field in report.target.fields
            ),
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        cover_sources=report.cover_sources,
    )


def _list_preview(field, option, current, alternatives=1, state=None):
    from mechanical_librarian.domain.metadata_report import TargetDestination

    destination = TargetDestination[field.upper()]
    source = _report()
    state = state or (
        TargetFieldState.MISSING if current is None else TargetFieldState.VALUE
    )
    report = MetadataReport.create(
        target=TargetFacts(
            source.target.connection,
            source.target.library,
            source.target.item,
            tuple(
                TargetField(value.destination, state, current)
                if value.destination is destination
                else value
                for value in source.target.fields
            ),
        ),
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )
    profile_options = dict(MetadataPolicyProfile.builtin().options)
    profile_options[field] = option
    profile = MetadataPolicyProfile.create(
        "Lists", profile_options, dict(MetadataPolicyProfile.builtin().destinations)
    )
    policy = resolve_policy(profile, report.target.library)
    base = evaluate_report(report)
    values = (("Fantasy", "Epic"), ("Mystery",))[:alternatives]
    alts = tuple(
        ProposalAlternative.create(
            ProposalValue(ProposalValueKind.LIST, value),
            ProposalAssessment.FILLS_EMPTY
            if current is None
            else ProposalAssessment.REPLACES_TARGET,
            ("provider_" + chr(97 + index) * 64,),
            ("correlation_" + chr(97 + index) * 64,),
            ProposalScope.WORK,
        )
        for index, value in enumerate(values)
    )
    ordered = tuple(sorted(alts, key=lambda value: value.alternative_id))
    result = ProposalResult(
        destination,
        ProposalOutcome.PROPOSE_CHANGE
        if alternatives == 1
        else ProposalOutcome.UNRESOLVED_CHOICE,
        ordered,
        ordered[0].alternative_id if alternatives == 1 else None,
        ("unique_typed_candidate",) if alternatives == 1 else ("competing_evidence",),
        next(
            value.dependencies
            for value in base.results
            if value.destination is destination
        ),
    )
    proposal = ProposalSet.create(
        report_id=base.report_id,
        target_fingerprint=base.target_fingerprint,
        results=tuple(
            result if value.destination is destination else value
            for value in base.results
        ),
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=base.bibliographic_combinations,
    )
    return next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == field
    )


def test_exact_pair_compiles_all_eight_fields_in_closed_order() -> None:
    report = _report()
    proposal = evaluate_report(report)
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    preview = compile_rich_metadata_preview(policy, report, proposal)
    assert (
        "subtitle",
        "series",
        "genres",
        "tags",
        "description_fill",
        "isbn_form",
        "asin",
        "explicit",
    ) == tuple(field.field for field in preview.fields)
    assert "policy_off" == preview.fields[3].reason
    assert "explicit_unsupported" == preview.fields[-1].reason


def test_binding_mismatch_fails_closed() -> None:
    report = _report()
    proposal = evaluate_report(report)
    with pytest.raises(RichMetadataPreviewError):
        compile_rich_metadata_preview(
            resolve_policy(MetadataPolicyProfile.builtin(), "other"), report, proposal
        )


def test_rich_automatic_and_ignore_are_typed_abstentions() -> None:
    report = _report()
    proposal = evaluate_report(report)
    options = dict(MetadataPolicyProfile.builtin().options)
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["genres"] = "automatic"
    destinations["tags"] = "ignore"
    profile = MetadataPolicyProfile.create("Manual rich preview", options, destinations)
    policy = resolve_policy(profile, report.target.library)
    # Rebind only in this direct pure-module proof; evidence/results remain intact.
    rebound = proposal.create(
        report_id=proposal.report_id,
        target_fingerprint=proposal.target_fingerprint,
        results=proposal.results,
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=proposal.bibliographic_combinations,
    )
    fields = {
        field.field: field
        for field in compile_rich_metadata_preview(policy, report, rebound).fields
    }
    assert "no_candidate" == fields["genres"].reason
    assert "policy_ignored" == fields["tags"].reason


def test_every_closed_rich_option_is_stably_exposed() -> None:
    report = _report()
    base = MetadataPolicyProfile.builtin()
    for field, options in {
        "subtitle": ("fill_empty", "corroborated_split"),
        "series": ("primary_narrative", "source_order", "all_qualified"),
        "genres": ("fill_empty", "add", "replace"),
        "tags": ("off", "fill_empty", "add", "replace"),
        "description_fill": ("safe_plain_text", "google_visible_text"),
        "isbn_form": ("preserve_exact", "prefer_isbn13"),
        "asin": ("preserve_exact", "ebook_manifestation"),
        "explicit": ("unsupported",),
    }.items():
        for option in options:
            choices = dict(base.options)
            choices[field] = option
            profile = MetadataPolicyProfile.create(
                "Matrix", choices, dict(base.destinations)
            )
            policy = resolve_policy(profile, report.target.library)
            proposal = evaluate_report(report)
            rebound = proposal.create(
                report_id=proposal.report_id,
                target_fingerprint=proposal.target_fingerprint,
                results=proposal.results,
                resolved_policy_id=policy.resolved_id,
                bibliographic_combinations=proposal.bibliographic_combinations,
            )
            preview = compile_rich_metadata_preview(policy, report, rebound)
            rendered = preview.to_data()
            assert rendered == preview.to_data()
            assert option == next(
                value.option for value in preview.fields if value.field == field
            )


def test_unusable_target_states_abstain_before_candidate_interpretation() -> None:
    base = _report()
    destinations = {
        "subtitle": "SUBTITLE",
        "series": "SERIES",
        "genres": "GENRES",
        "tags": "TAGS",
        "description_fill": "DESCRIPTION",
        "isbn_form": "ISBN",
        "asin": "ASIN",
    }
    from mechanical_librarian.domain.metadata_report import TargetDestination

    for state in (
        TargetFieldState.UNLOADED,
        TargetFieldState.MALFORMED,
        TargetFieldState.OVERSIZED,
    ):
        for field, destination_name in destinations.items():
            report = _with_state(base, TargetDestination[destination_name], state)
            policy = resolve_policy(
                MetadataPolicyProfile.builtin(), report.target.library
            )
            proposal = evaluate_report(report)
            rebound = proposal.create(
                report_id=proposal.report_id,
                target_fingerprint=proposal.target_fingerprint,
                results=proposal.results,
                resolved_policy_id=policy.resolved_id,
                bibliographic_combinations=proposal.bibliographic_combinations,
            )
            preview = compile_rich_metadata_preview(policy, report, rebound)
            selected = next(value for value in preview.fields if value.field == field)
            assert "target_state_unusable" == selected.reason
            assert "abstain" == selected.outcome


@pytest.mark.parametrize(
    ("field", "destination", "reason"),
    (
        ("genres", "ignore", "policy_ignored"),
        ("genres", "automatic", "no_candidate"),
        ("tags", "ask", "policy_off"),
    ),
    ids=("genres_ignore", "genres_automatic", "tags_ask"),
)
def test_destination_precedence_is_typed_and_never_projects(
    field: str, destination: str, reason: str
) -> None:
    report = _report()
    base = MetadataPolicyProfile.builtin()
    choices = dict(base.destinations)
    choices[field] = destination
    profile = MetadataPolicyProfile.create("Destination", dict(base.options), choices)
    policy = resolve_policy(profile, report.target.library)
    proposal = evaluate_report(report)
    rebound = proposal.create(
        report_id=proposal.report_id,
        target_fingerprint=proposal.target_fingerprint,
        results=proposal.results,
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=proposal.bibliographic_combinations,
    )
    selected = next(
        value
        for value in compile_rich_metadata_preview(policy, report, rebound).fields
        if value.field == field
    )
    assert reason == selected.reason
    assert "abstain" == selected.outcome


def test_tags_off_precedes_absent_or_ambiguous_candidates() -> None:
    report = _report()
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    proposal = evaluate_report(report)
    rebound = proposal.create(
        report_id=proposal.report_id,
        target_fingerprint=proposal.target_fingerprint,
        results=proposal.results,
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=proposal.bibliographic_combinations,
    )
    tag = next(
        value
        for value in compile_rich_metadata_preview(policy, report, rebound).fields
        if value.field == "tags"
    )
    assert ("policy_off", "abstain", None) == (
        tag.reason,
        tag.outcome,
        tag.desired_value,
    )


def test_list_fill_add_replace_and_no_ops_preserve_exact_order() -> None:
    assert ("projected", ("Fantasy", "Epic")) == (
        _list_preview("genres", "fill_empty", None).outcome,
        _list_preview("genres", "fill_empty", None).desired_value,
    )
    filled = _list_preview("genres", "fill_empty", ("Existing",))
    assert ("no_op", ("Existing",), "target_preserved") == (
        filled.outcome,
        filled.desired_value,
        filled.reason,
    )
    added = _list_preview("genres", "add", ("Epic", "Existing"))
    assert ("Epic", "Existing", "Fantasy") == added.desired_value
    replaced = _list_preview("tags", "replace", ("Existing",))
    assert ("Fantasy", "Epic") == replaced.desired_value
    unchanged = _list_preview("tags", "add", ("Fantasy", "Epic"))
    assert ("no_op", "unchanged") == (unchanged.outcome, unchanged.reason)


@pytest.mark.parametrize(
    "state",
    (
        TargetFieldState.MISSING,
        TargetFieldState.NULL,
        TargetFieldState.EMPTY,
    ),
    ids=("missing", "null", "empty"),
)
def test_genre_fill_empty_projects_each_supported_empty_target_state(
    state,
) -> None:
    preview = _list_preview("genres", "fill_empty", None, state=state)
    assert (state.value, "projected", ("Fantasy", "Epic")) == (
        preview.target_state,
        preview.outcome,
        preview.desired_value,
    )


@pytest.mark.parametrize("field", ("genres", "tags"))
def test_list_add_abstains_when_exact_target_union_would_be_oversized(
    field,
) -> None:
    current = tuple(f"Existing {index}" for index in range(32))
    preview = _list_preview(field, "add", current)
    assert ("abstain", "target_projection_oversized", None) == (
        preview.outcome,
        preview.reason,
        preview.desired_value,
    )


def test_automatic_list_projection_rejects_an_oversized_union() -> None:
    candidate = {"kind": "list", "value": ["New value"]}
    with pytest.raises(
        RichMetadataPreviewError, match="rich automatic projection is oversized"
    ):
        project_f20_automatic_decision_value(
            "genres",
            "add",
            candidate,
            tuple(f"Existing {index}" for index in range(32)),
        )


def test_populated_list_modes_use_real_unresolved_evaluator_candidate() -> None:
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
    report = _report(
        fields=_fields(genres=genres),
        providers=(_provider("catalogue", "edition-1", (claim,)),),
    )
    for option, expected in (
        ("add", ("Fantasy", "Mystery")),
        ("replace", ("Mystery",)),
    ):
        options = dict(MetadataPolicyProfile.builtin().options)
        options["genres"] = option
        policy = resolve_policy(
            MetadataPolicyProfile.create(
                "Populated list",
                options,
                dict(MetadataPolicyProfile.builtin().destinations),
            ),
            report.target.library,
        )
        proposal = evaluate_report(
            report, policy.resolved_id, dict(policy.destinations)
        )
        result = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.GENRES
        )
        assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
        field = next(
            value
            for value in compile_rich_metadata_preview(policy, report, proposal).fields
            if value.field == "genres"
        )
        assert ("projected", expected) == (field.outcome, field.desired_value)


def test_list_ambiguity_never_synthesizes_separate_alternatives() -> None:
    value = _list_preview("genres", "replace", None, alternatives=2)
    assert ("abstain", "ambiguous_evidence", None) == (
        value.outcome,
        value.reason,
        value.desired_value,
    )


def test_corroborated_split_uses_real_admitted_candidate_members() -> None:
    report = _bibliographic_report(
        current_title="Secret History: A Mistborn Novella",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),
        ),
    )
    base = evaluate_report(report)
    assert base.bibliographic_combinations
    options = dict(MetadataPolicyProfile.builtin().options)
    options["subtitle"] = "corroborated_split"
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Split", options, dict(MetadataPolicyProfile.builtin().destinations)
        ),
        report.target.library,
    )
    proposal = base.create(
        report_id=base.report_id,
        target_fingerprint=base.target_fingerprint,
        results=base.results,
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=base.bibliographic_combinations,
    )
    selected = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )
    assert {
        "title": "Secret History",
        "subtitle": "A Mistborn Novella",
    } == selected.desired_value


def test_corroborated_split_rejects_unbounded_or_absent_separator() -> None:
    report = _bibliographic_report(
        current_title="Secret History / A Mistborn Novella",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),
        ),
    )
    options = dict(MetadataPolicyProfile.builtin().options)
    options["subtitle"] = "corroborated_split"
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Bounded split",
            options,
            dict(MetadataPolicyProfile.builtin().destinations),
        ),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    selected = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )
    assert ("abstain", "corroborated_split_unavailable", None) == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_series_options_are_closed_and_primary_without_combination_abstains(
    option: str,
) -> None:
    report = _report()
    base = evaluate_report(report)
    options = dict(MetadataPolicyProfile.builtin().options)
    options["series"] = option
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Series", options, dict(MetadataPolicyProfile.builtin().destinations)
        ),
        report.target.library,
    )
    proposal = base.create(
        report_id=base.report_id,
        target_fingerprint=base.target_fingerprint,
        results=base.results,
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=base.bibliographic_combinations,
    )
    field = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "series"
    )
    if option == "primary_narrative" and not base.bibliographic_combinations:
        assert "no_candidate" == field.reason
    assert option == field.option


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_series_modes_use_one_admitted_tuple_and_never_synthesize(option: str) -> None:
    report = _report()
    base = evaluate_report(report)
    targets = {value.destination: value for value in report.target.fields}
    alt = ProposalAlternative.create(
        ProposalValue(
            ProposalValueKind.SERIES,
            (
                TargetSeries("Reading Order", "2"),
                TargetSeries("Publication Order", None),
            ),
        ),
        ProposalAssessment.FILLS_EMPTY,
        ("provider_" + "a" * 64,),
        ("correlation_" + "a" * 64,),
        ProposalScope.WORK,
    )
    result = ProposalResult(
        TargetDestination.SERIES,
        ProposalOutcome.PROPOSE_CHANGE,
        (alt,),
        alt.alternative_id,
        ("unique_typed_candidate",),
        next(
            value.dependencies
            for value in base.results
            if value.destination is TargetDestination.SERIES
        ),
    )
    combo = BibliographicCombination.create(
        (
            BibliographicSelection.preserve(targets[TargetDestination.TITLE]),
            BibliographicSelection.preserve(targets[TargetDestination.SUBTITLE]),
            BibliographicSelection.candidate(
                TargetDestination.SERIES, alt.alternative_id
            ),
        ),
        ("representation_" + "a" * 64,),
    )
    options = dict(MetadataPolicyProfile.builtin().options)
    options["series"] = option
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["series"] = "ask"
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Series fixture",
            options,
            destinations,
        ),
        report.target.library,
    )
    proposal = ProposalSet.create(
        report_id=base.report_id,
        target_fingerprint=base.target_fingerprint,
        results=tuple(
            result if value.destination is TargetDestination.SERIES else value
            for value in base.results
        ),
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=(combo,),
    )
    field = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "series"
    )
    assert ({"name": "Reading Order", "sequence": "2"},) == field.desired_value
    assert alt.alternative_id == field.source_candidate_id
    if option == "all_qualified":
        assert field.narrowed_for_target
        assert (
            {"name": "Reading Order", "sequence": "2"},
            {"name": "Publication Order", "sequence": None},
        ) == field.qualified_value


@pytest.mark.parametrize(
    "option",
    ("primary_narrative", "source_order", "all_qualified"),
)
def test_automatic_series_projection_preserves_qualified_memberships_and_prefers_one(
    option,
) -> None:
    candidate = {
        "kind": "series",
        "value": [
            {"name": "Reading Order", "sequence": "2"},
            {"name": "Publication Order", "sequence": None},
        ],
    }
    assert {
        "kind": "series",
        "value": [
            {"name": "Reading Order", "sequence": "2"},
        ],
    } == project_f20_automatic_decision_value("series", option, candidate)
    assert 2 == len(candidate["value"])


@pytest.mark.parametrize("option", ("source_order", "all_qualified"))
def test_preserving_single_series_is_a_no_op_for_source_order_and_all_qualified(
    option: str,
) -> None:
    base = _report()
    current = (TargetSeries("Exact Provider Spelling", "2"),)
    report = MetadataReport.create(
        target=TargetFacts(
            base.target.connection,
            base.target.library,
            base.target.item,
            tuple(
                TargetField(value.destination, TargetFieldState.VALUE, current)
                if value.destination is TargetDestination.SERIES
                else value
                for value in base.target.fields
            ),
        ),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        cover_sources=base.cover_sources,
    )
    base_proposal = evaluate_report(report)
    alternative = ProposalAlternative.create(
        ProposalValue(ProposalValueKind.SERIES, current),
        ProposalAssessment.PRESERVES_TARGET,
        ("provider_" + "a" * 64,),
        ("correlation_" + "a" * 64,),
        ProposalScope.WORK,
    )
    result = ProposalResult(
        TargetDestination.SERIES,
        ProposalOutcome.KEEP,
        (alternative,),
        None,
        ("positive_preservation_evidence",),
        next(
            value.dependencies
            for value in base_proposal.results
            if value.destination is TargetDestination.SERIES
        ),
    )
    options = dict(MetadataPolicyProfile.builtin().options)
    options["series"] = option
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["series"] = "ask"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Keep", options, destinations),
        report.target.library,
    )
    proposal = ProposalSet.create(
        report_id=base_proposal.report_id,
        target_fingerprint=base_proposal.target_fingerprint,
        results=tuple(
            result if value.destination is TargetDestination.SERIES else value
            for value in base_proposal.results
        ),
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=base_proposal.bibliographic_combinations,
    )
    field = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "series"
    )
    assert ("no_op", "unchanged", False) == (
        field.outcome,
        field.reason,
        field.narrowed_for_target,
    )


def test_keep_series_requires_one_preserving_membership() -> None:
    alternative = ProposalAlternative.create(
        ProposalValue(
            ProposalValueKind.SERIES, (TargetSeries("Not preserving", None),)
        ),
        ProposalAssessment.FILLS_EMPTY,
        ("provider_" + "a" * 64,),
        ("correlation_" + "a" * 64,),
        ProposalScope.WORK,
    )
    with pytest.raises(ProposalValueError):
        ProposalResult(
            TargetDestination.SERIES,
            ProposalOutcome.KEEP,
            (alternative,),
            None,
            ("positive_preservation_evidence",),
        )


def test_automatic_series_records_two_independent_agreeing_provider_lineages() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider("search", "edition-2", "open_library"),
    )

    field = _automatic_series_preview(report)

    assert ("projected", None, ({"name": "Reading Order", "sequence": "2"},)) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_abstains_without_an_explicit_membership_position() -> None:
    report = _automatic_series_report(
        _series_provider(
            "catalogue",
            "edition-1",
            "hardcover",
            _series_claims("catalogue", "edition-1")[:1],
        ),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            _series_claims("search", "edition-2")[:1],
        ),
    )

    manual = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.SERIES
    )
    field = _automatic_series_preview(report)

    assert ProposalOutcome.PROPOSE_CHANGE is manual.outcome
    assert ("abstain", "series_membership_incomplete", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_abstains_for_one_unpositioned_matching_membership() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            _series_claims("search", "edition-2")[:1],
        ),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_membership_incomplete", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_keeps_different_name_missing_position_as_conflict() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            _series_claims("search", "edition-2", name="Other Order")[:1],
        ),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_conflicting_membership", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_pairs_absent_sequence_with_its_own_membership() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            (
                *_series_claims(
                    "search", "edition-2", name="Other Order", sequence="1"
                ),
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.SERIES,
                    "Reading Order",
                    scope=ClaimScope.WORK,
                    order=2,
                ),
                ProviderClaim.create(
                    provider="search",
                    candidate="edition-2",
                    kind=ClaimKind.SERIES_SEQUENCE,
                    value=None,
                    state=FieldState.ABSENT,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    raw_order=3,
                ),
            ),
        ),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_membership_incomplete", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_ignores_interleaved_nonseries_claims_when_pairing() -> None:
    report = _automatic_series_report(
        _series_provider(
            "catalogue",
            "edition-1",
            "hardcover",
            (
                _series_claims("catalogue", "edition-1")[0],
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.TITLE,
                    "Ignored",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
                _series_claims("catalogue", "edition-1")[1],
            ),
        ),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            (
                _series_claims("search", "edition-2")[0],
                _claim(
                    "search",
                    "edition-2",
                    ClaimKind.TITLE,
                    "Ignored",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
                _series_claims("search", "edition-2")[1],
            ),
        ),
    )

    field = _automatic_series_preview(report)

    assert "projected" == field.outcome


def test_automatic_series_abstains_when_agreement_has_one_provider_lineage() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider("mirror", "edition-2", "hardcover"),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_independent_corroboration_required", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_abstains_with_a_stable_conflict_reason() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            _series_claims("search", "edition-2", sequence="3"),
        ),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_conflicting_membership", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_abstains_when_provider_observation_failed() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider("search", "edition-2", "open_library"),
        ProviderObservation("catalogue_backup", ProviderStatus.UNAVAILABLE),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_provider_failure", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_abstains_when_a_candidate_provider_has_a_failed_lane() -> (
    None
):
    base = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider("search", "edition-2", "open_library"),
    )
    local = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Current title",
        canonical="Current title",
        excerpt="Current title",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    provider = base.providers[0]
    candidate = provider.candidates[0]
    successful_lane = ProviderLaneObservation(
        "lane_" + "a" * 64,
        ProviderQueryKind.WORK,
        ProviderStatus.CANDIDATES,
        (local.reference,),
        candidates=(
            ProviderLaneCandidate(
                provider_lane_candidate_reference(
                    provider.provider,
                    "lane_" + "a" * 64,
                    candidate.identity,
                    candidate.work_identity,
                ),
                candidate.identity,
                candidate.work_identity,
            ),
        ),
    )
    failed_lane = ProviderLaneObservation(
        "lane_" + "c" * 64,
        ProviderQueryKind.WORK,
        ProviderStatus.UNAVAILABLE,
        (local.reference,),
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=(local,),
        local_reasons=("no_opening_matter",),
        providers=(
            replace(
                provider,
                input_references=(local.reference,),
                query_kind=ProviderQueryKind.DISCOVERY,
                lanes=(successful_lane, failed_lane),
            ),
            base.providers[1],
        ),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_provider_failure", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_abstains_when_membership_is_materially_incomplete() -> None:
    incomplete = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.SERIES_SEQUENCE,
        value=None,
        state=FieldState.MALFORMED,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=1,
    )
    report = _automatic_series_report(
        _series_provider(
            "catalogue",
            "edition-1",
            "hardcover",
            (_series_claims("catalogue", "edition-1")[0], incomplete),
        ),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_membership_incomplete", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_description_options_use_exact_admitted_text_and_google_evidence() -> None:
    transformed = _claim(
        "google",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "2 < 3",
        scope=ClaimScope.WORK,
    )
    google = _report(
        providers=(
            _provider(
                "google",
                "edition-1",
                (transformed,),
                (
                    ProviderEvidence(
                        ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                        "google-books-html-visible-text-v1",
                        ClaimScope.WORK,
                    ),
                ),
                authority="google_books",
            ),
        )
    )
    visible = _preview_field(google, "description_fill", "google_visible_text")
    assert ("projected", "2 < 3", None) == (
        visible.outcome,
        visible.desired_value,
        visible.reason,
    )
    assert visible.source_candidate_id is not None
    assert (transformed.reference,) == visible.source_evidence_references
    safe_google = _preview_field(google, "description_fill", "safe_plain_text")
    assert ("abstain", "option_evidence_unavailable") == (
        safe_google.outcome,
        safe_google.reason,
    )

    safe = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "Bounded plain description",
        scope=ClaimScope.WORK,
    )
    ordinary = _report(providers=(_provider("catalogue", "edition-1", (safe,)),))
    plain = _preview_field(ordinary, "description_fill", "safe_plain_text")
    unavailable = _preview_field(ordinary, "description_fill", "google_visible_text")
    assert "Bounded plain description" == plain.desired_value
    assert plain.source_candidate_id is not None
    assert (safe.reference,) == plain.source_evidence_references
    assert ("abstain", "option_evidence_unavailable") == (
        unavailable.outcome,
        unavailable.reason,
    )

    corroborated = _report(
        providers=(
            _provider(
                "google",
                "edition-1",
                (transformed,),
                (
                    ProviderEvidence(
                        ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                        "google-books-html-visible-text-v1",
                        ClaimScope.WORK,
                    ),
                ),
                authority="google_books",
            ),
            _provider(
                "open_library",
                "edition-2",
                (
                    _claim(
                        "open_library",
                        "edition-2",
                        ClaimKind.DESCRIPTION,
                        "2 < 3",
                        scope=ClaimScope.WORK,
                    ),
                ),
            ),
        )
    )
    mixed = _preview_field(corroborated, "description_fill", "google_visible_text")
    assert ("projected", "2 < 3", None) == (
        mixed.outcome,
        mixed.desired_value,
        mixed.reason,
    )
    assert (transformed.reference,) == mixed.source_evidence_references


@pytest.mark.parametrize(
    "option",
    ("safe_plain_text", "google_visible_text"),
)
def test_plain_description_requires_exact_non_transformed_source(
    option,
) -> None:
    transformed = _claim(
        "alpha_google",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "Shared  description",
        scope=ClaimScope.WORK,
    )
    ordinary = _claim(
        "zulu",
        "edition-2",
        ClaimKind.DESCRIPTION,
        "Shared description",
        scope=ClaimScope.WORK,
    )
    normalized_equivalent = _report(
        providers=(
            _provider(
                "alpha_google",
                "edition-1",
                (transformed,),
                (
                    ProviderEvidence(
                        ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                        "google-books-html-visible-text-v1",
                        ClaimScope.WORK,
                    ),
                ),
                authority="google_books",
            ),
            _provider("zulu", "edition-2", (ordinary,)),
        )
    )
    safe = _preview_field(normalized_equivalent, "description_fill", "safe_plain_text")
    assert ("abstain", "option_evidence_unavailable") == (safe.outcome, safe.reason)

    transformed_elsewhere = _claim(
        "catalogue",
        "edition-3",
        ClaimKind.DESCRIPTION,
        "Transformed elsewhere",
        scope=ClaimScope.WORK,
    )
    non_google_transform = _report(
        providers=(
            _provider(
                "catalogue",
                "edition-3",
                (transformed_elsewhere,),
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
    result = _preview_field(non_google_transform, "description_fill", option)
    assert ("abstain", "option_evidence_unavailable") == (result.outcome, result.reason)


def test_google_description_requires_transform_and_authority() -> None:
    claim = _claim(
        "google",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "Plain text",
        scope=ClaimScope.WORK,
    )
    for provider in (
        _provider("google", "edition-1", (claim,), authority="google_books"),
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
    ):
        field = _preview_field(
            _report(providers=(provider,)),
            "description_fill",
            "google_visible_text",
        )
        assert "option_evidence_unavailable" == field.reason


def test_description_preserves_populated_and_abstains_on_ambiguity_or_absence() -> None:
    populated = _report(
        fields=_fields(
            description=TargetField(
                TargetDestination.DESCRIPTION,
                TargetFieldState.VALUE,
                "Owner description",
            )
        )
    )
    preserved = _preview_field(populated, "description_fill", "google_visible_text")
    assert ("no_op", "Owner description", "target_preserved") == (
        preserved.outcome,
        preserved.desired_value,
        preserved.reason,
    )

    left = _claim(
        "alpha",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "First",
        scope=ClaimScope.WORK,
    )
    right = _claim(
        "beta",
        "edition-2",
        ClaimKind.DESCRIPTION,
        "Second",
        scope=ClaimScope.WORK,
    )
    ambiguous = _preview_field(
        _report(
            providers=(
                _provider("alpha", "edition-1", (left,)),
                _provider("beta", "edition-2", (right,)),
            )
        ),
        "description_fill",
        "safe_plain_text",
    )
    absent = _preview_field(_report(), "description_fill", "safe_plain_text")
    assert "ambiguous_evidence" == ambiguous.reason
    assert "no_candidate" == absent.reason


def test_isbn_options_preserve_or_select_only_exact_admitted_forms() -> None:
    populated = _report(
        fields=_fields(
            isbn=TargetField(
                TargetDestination.ISBN, TargetFieldState.VALUE, "0306406152"
            )
        )
    )
    preserved = _preview_field(populated, "isbn_form", "prefer_isbn13")
    assert ("no_op", "0306406152", "target_preserved") == (
        preserved.outcome,
        preserved.desired_value,
        preserved.reason,
    )

    isbn10 = _local_isbn("0306406152", "valid_isbn_10")
    ten_report = _report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(isbn10,),
    )
    exact = _preview_field(ten_report, "isbn_form", "preserve_exact")
    unavailable = _preview_field(ten_report, "isbn_form", "prefer_isbn13")
    assert "0306406152" == exact.desired_value
    assert ("abstain", "option_evidence_unavailable", None) == (
        unavailable.outcome,
        unavailable.reason,
        unavailable.desired_value,
    )

    isbn13 = _local_isbn("9780306406157", "valid_isbn_13")
    thirteen_report = _report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(isbn13,),
    )
    preferred = _preview_field(thirteen_report, "isbn_form", "prefer_isbn13")
    assert "9780306406157" == preferred.desired_value


def test_isbn_family_preview_selects_exact_local_or_isbn13_representation() -> None:
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
    report = _report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(local,),
        providers=(provider,),
    )

    exact = _preview_field(report, "isbn_form", "preserve_exact")
    preferred = _preview_field(report, "isbn_form", "prefer_isbn13")

    assert ("projected", "0306406152") == (exact.outcome, exact.desired_value)
    assert ("projected", "9780306406157") == (
        preferred.outcome,
        preferred.desired_value,
    )
    assert 2 == len(exact.source_evidence_references)
    assert exact.source_evidence_references == preferred.source_evidence_references
    assert exact == _preview_field(report, "isbn_form", "preserve_exact")

    local13 = _local_isbn("9780306406157", "valid_isbn_13")
    family_provider = _provider(
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
    family_report = _report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(local13,),
        providers=(family_provider,),
    )
    for option in ("preserve_exact", "prefer_isbn13"):
        field = _preview_field(family_report, "isbn_form", option)
        assert ("projected", "9780306406157") == (
            field.outcome,
            field.desired_value,
        )
        assert 3 == len(field.source_evidence_references)


def test_isbn_family_preview_rejects_distinct_or_invalid_identifiers() -> None:
    local = _local_isbn("0306406152", "valid_isbn_10")
    other_family = _local_isbn("9780140328721", "valid_isbn_13")
    distinct = _report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(local, other_family),
    )
    for option in ("preserve_exact", "prefer_isbn13"):
        field = _preview_field(distinct, "isbn_form", option)
        assert ("abstain", "ambiguous_evidence", None) == (
            field.outcome,
            field.reason,
            field.desired_value,
        )

    with pytest.raises(ValueError):
        _local_isbn("0306406153", "invalid_checksum")


def test_isbn13_preference_abstains_on_two_exact_admitted_forms() -> None:
    report = _report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(
            _local_isbn("9780140328721", "valid_isbn_13"),
            _local_isbn("9780306406157", "valid_isbn_13"),
        ),
    )
    field = _preview_field(report, "isbn_form", "prefer_isbn13")
    assert ("abstain", "ambiguous_evidence", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_asin_options_preserve_exact_text_and_require_ebook_manifestation() -> None:
    populated = _report(
        fields=_fields(
            asin=TargetField(
                TargetDestination.ASIN, TargetFieldState.VALUE, "B012345678"
            )
        )
    )
    preserved = _preview_field(populated, "asin", "ebook_manifestation")
    assert ("no_op", "B012345678", "target_preserved") == (
        preserved.outcome,
        preserved.desired_value,
        preserved.reason,
    )

    ebook = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.ASIN,
        "B012345678",
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    report = _report(
        fields=_fields(
            asin=TargetField(TargetDestination.ASIN, TargetFieldState.MISSING)
        ),
        providers=(_provider("catalogue", "edition-1", (ebook,)),),
    )
    exact = _preview_field(report, "asin", "preserve_exact")
    manifestation = _preview_field(report, "asin", "ebook_manifestation")
    assert "B012345678" == exact.desired_value
    assert "B012345678" == manifestation.desired_value

    unknown = _claim(
        "catalogue",
        "edition-2",
        ClaimKind.ASIN,
        "B087654321",
        scope=ClaimScope.EDITION,
        applicability="unknown",
    )
    hostile_report = _report(
        fields=_fields(
            asin=TargetField(TargetDestination.ASIN, TargetFieldState.MISSING)
        ),
        providers=(_provider("catalogue", "edition-2", (unknown,)),),
    )
    options = dict(MetadataPolicyProfile.builtin().options)
    options["asin"] = "ebook_manifestation"
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "ASIN evidence gate",
            options,
            dict(MetadataPolicyProfile.builtin().destinations),
        ),
        hostile_report.target.library,
    )
    base = evaluate_report(
        hostile_report, policy.resolved_id, dict(policy.destinations)
    )
    alternative = ProposalAlternative.create(
        ProposalValue(ProposalValueKind.TEXT, "B087654321"),
        ProposalAssessment.FILLS_EMPTY,
        (unknown.reference,),
        ("correlation_" + "a" * 64,),
        ProposalScope.EDITION,
    )
    result = ProposalResult(
        TargetDestination.ASIN,
        ProposalOutcome.PROPOSE_CHANGE,
        (alternative,),
        alternative.alternative_id,
        ("unique_typed_candidate",),
        next(
            value.dependencies
            for value in base.results
            if value.destination is TargetDestination.ASIN
        ),
    )
    proposal = ProposalSet.create(
        report_id=base.report_id,
        target_fingerprint=base.target_fingerprint,
        results=tuple(
            result if value.destination is TargetDestination.ASIN else value
            for value in base.results
        ),
        resolved_policy_id=policy.resolved_id,
        bibliographic_combinations=base.bibliographic_combinations,
    )
    rejected = next(
        value
        for value in compile_rich_metadata_preview(
            policy, hostile_report, proposal
        ).fields
        if value.field == "asin"
    )
    assert "option_evidence_unavailable" == rejected.reason
