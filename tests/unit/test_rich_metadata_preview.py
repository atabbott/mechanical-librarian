"""Direct contract proof for the no-effect rich metadata preview compiler."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    IdentifierEvidenceBasis,
    IdentifierRelation,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
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
    select_isbn_representation,
)
from tests.support.proposal_evidence import (
    _claim,
    _exact_edition_cover,
    _fields,
    _local_isbn,
    _local_subtitle,
    _provider,
    _report,
    _result,
    _target_work_candidate,
    _target_work_report,
)
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source


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


def _isbn_target_work_candidate(
    *isbns: str, include_work_identity: bool = False
) -> ProviderCandidate:
    candidate = _target_work_candidate("catalogue", "edition-1", "work-1")
    retained = tuple(
        claim
        for claim in candidate.claims
        if claim.kind is not ClaimKind.ISBN
        and (
            include_work_identity
            or claim.kind not in {ClaimKind.TITLE, ClaimKind.CREATOR}
        )
    )
    return replace(
        candidate,
        claims=(
            *retained,
            *(
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    value,
                    scope=ClaimScope.EDITION,
                    order=len(retained) + index,
                    applicability="ebook",
                )
                for index, value in enumerate(isbns)
            ),
        ),
    )


def _target_work_preview(report: MetadataReport):
    options = dict(MetadataPolicyProfile.builtin().options)
    options.update(
        {
            "description_fill": "safe_plain_text",
            "genres": "fill_empty",
            "series": "source_order",
            "tags": "fill_empty",
        }
    )
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations.update(
        {
            "description_fill": "automatic",
            "genres": "automatic",
            "series": "automatic",
            "tags": "automatic",
        }
    )
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Target work applicability", options, destinations
        ),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, destinations)
    preview = compile_rich_metadata_preview(policy, report, proposal)
    return proposal, {
        field: next(value for value in preview.fields if value.field == field)
        for field in ("description_fill", "series", "genres", "tags")
    }


def _description_candidate(
    provider: str,
    identity: str,
    work_identity: str,
    description: str,
) -> ProviderCandidate:
    candidate = _target_work_candidate(provider, identity, work_identity)
    return replace(
        candidate,
        claims=tuple(
            _claim(
                provider,
                identity,
                ClaimKind.DESCRIPTION,
                description,
                scope=ClaimScope.WORK,
                order=claim.raw_order,
            )
            if claim.kind is ClaimKind.DESCRIPTION
            else claim
            for claim in candidate.claims
        ),
    )


_QUALIFIED_DESCRIPTION = (
    "This complete provider description contains enough specific words to clear "
    "the conservative quality floor while remaining ordinary readable book prose."
)


def test_exact_multi_isbn_work_keeps_all_automatic_work_fields() -> None:
    catalogue = _target_work_candidate("catalogue", "edition-1", "work-1")
    search = _target_work_candidate("search", "edition-2", "work-1")
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("catalogue", "hardcover", (catalogue,), True),
                ("search", "open_library", (search,), True),
            )
        )
    )

    assert {field: preview.outcome for field, preview in fields.items()} == {
        "description_fill": "projected",
        "series": "projected",
        "genres": "projected",
        "tags": "projected",
    }


def test_automatic_description_filters_applicability_before_provider_order() -> None:
    unproved = _description_candidate(
        "preferred",
        "edition-1",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Preferred source.",
    )
    applicable = _description_candidate(
        "fallback",
        "edition-2",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Applicable source.",
    )
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("preferred", "hardcover", (unproved,), False),
                ("fallback", "open_library", (applicable,), True),
            )
        )
    )

    description = fields["description_fill"]
    assert ("projected", applicable.claims[3].value) == (
        description.outcome,
        description.desired_value,
    )
    assert description.source_evidence_references == (applicable.claims[3].reference,)


def test_automatic_description_excludes_unproved_source_for_one_collapsed_value() -> None:
    shared_description = _QUALIFIED_DESCRIPTION + " Shared source text."
    unproved = _description_candidate(
        "preferred", "edition-1", "work-1", shared_description
    )
    applicable = _description_candidate(
        "fallback", "edition-2", "work-1", shared_description
    )
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("preferred", "hardcover", (unproved,), False),
                ("fallback", "google_books", (applicable,), True),
            )
        )
    )

    description = fields["description_fill"]
    assert ("projected", shared_description) == (
        description.outcome,
        description.desired_value,
    )
    assert description.source_evidence_references == (applicable.claims[3].reference,)


def test_single_automatic_description_preserves_the_preexisting_no_floor_path() -> None:
    candidate = _description_candidate(
        "catalogue",
        "edition-1",
        "work-1",
        "One short but otherwise valid description.",
    )
    _, fields = _target_work_preview(
        _target_work_report((("catalogue", "hardcover", (candidate,), True),))
    )

    description = fields["description_fill"]
    assert ("projected", candidate.claims[3].value) == (
        description.outcome,
        description.desired_value,
    )
    assert description.source_evidence_references == (candidate.claims[3].reference,)


def test_automatic_description_applies_quality_floor_then_provider_order() -> None:
    too_short = _description_candidate(
        "preferred",
        "edition-1",
        "work-1",
        "A short description from the preferred provider.",
    )
    lower_authority = _description_candidate(
        "fallback",
        "edition-2",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Lower authority source.",
    )
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("fallback", "open_library", (lower_authority,), True),
                ("preferred", "hardcover", (too_short,), True),
            )
        )
    )

    description = fields["description_fill"]
    assert ("projected", lower_authority.claims[3].value) == (
        description.outcome,
        description.desired_value,
    )


def test_automatic_description_uses_fixed_authority_order_not_source_order() -> None:
    lower_authority = _description_candidate(
        "fallback",
        "edition-1",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Lower authority source.",
    )
    preferred = _description_candidate(
        "preferred",
        "edition-2",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Preferred source.",
    )
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("fallback", "open_library", (lower_authority,), True),
                ("preferred", "hardcover", (preferred,), True),
            )
        )
    )

    description = fields["description_fill"]
    assert ("projected", preferred.claims[3].value) == (
        description.outcome,
        description.desired_value,
    )


def test_automatic_description_abstains_from_equal_authority_competition() -> None:
    first = _description_candidate(
        "first",
        "edition-1",
        "work-1",
        _QUALIFIED_DESCRIPTION + " First source.",
    )
    second = _description_candidate(
        "second",
        "edition-2",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Second source.",
    )
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("first", "hardcover", (first,), True),
                ("second", "hardcover", (second,), True),
            )
        )
    )

    description = fields["description_fill"]
    assert ("abstain", "ambiguous_evidence") == (
        description.outcome,
        description.reason,
    )


@pytest.mark.parametrize(
    "description",
    (
        "A short description that cannot clear the frozen minimum length.",
        _QUALIFIED_DESCRIPTION + " This source visibly cuts off...",
    ),
)
def test_automatic_description_abstains_when_competing_text_misses_quality_floor(
    description: str,
) -> None:
    first = _description_candidate("first", "edition-1", "work-1", description)
    second_text = (
        "Another " + description
        if description.endswith("...")
        else description + " Brief."
    )
    second = _description_candidate("second", "edition-2", "work-1", second_text)
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("first", "hardcover", (first,), True),
                ("second", "open_library", (second,), True),
            )
        )
    )

    description_preview = fields["description_fill"]
    assert ("abstain", "description_quality_floor_not_met") == (
        description_preview.outcome,
        description_preview.reason,
    )


def test_isbn_intersection_does_not_replace_missing_work_identity() -> None:
    candidate = _isbn_target_work_candidate("9780306406157")

    proposal, fields = _target_work_preview(
        _target_work_report(
            (("catalogue", "hardcover", (candidate,), True),),
            target_authors=None,
        )
    )

    tags = next(
        result
        for result in proposal.results
        if result.destination is TargetDestination.TAGS
    )
    assert tags.alternatives
    assert ("abstain", "target_work_applicability_unproved") == (
        fields["tags"].outcome,
        fields["tags"].reason,
    )


def test_unusable_retained_isbn_does_not_conflict_with_matching_edition() -> None:
    candidate = _isbn_target_work_candidate("9780306406157", include_work_identity=True)
    unusable = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.ISBN,
        value=None,
        state=FieldState.ABSENT,
        scope=ClaimScope.EDITION,
        applicability="ebook",
        raw_order=len(candidate.claims),
    )
    candidate = replace(candidate, claims=(*candidate.claims, unusable))

    _, fields = _target_work_preview(
        _target_work_report(
            (("catalogue", "hardcover", (candidate,), True),),
        )
    )

    assert fields["tags"].outcome == "projected"


@pytest.mark.parametrize(
    "isbns",
    (
        ("9780140328721",),
        ("9780306406157", "9780140328721"),
    ),
    ids=("different_family", "conflicting_families"),
)
def test_nonintersecting_or_conflicting_isbns_remain_manual(
    isbns: tuple[str, ...],
) -> None:
    candidate = _isbn_target_work_candidate(*isbns, include_work_identity=True)

    proposal, fields = _target_work_preview(
        _target_work_report(
            (("catalogue", "hardcover", (candidate,), True),),
        )
    )

    tags = next(
        result
        for result in proposal.results
        if result.destination is TargetDestination.TAGS
    )
    assert tags.alternatives
    assert ("abstain", "target_work_applicability_unproved") == (
        fields["tags"].outcome,
        fields["tags"].reason,
    )


def test_only_tag_citations_exclude_retained_unproved_sources() -> None:
    catalogue = _target_work_candidate("catalogue", "edition-1", "work-1")
    search = _target_work_candidate("search", "edition-2", "work-1")
    unproved = _target_work_candidate("unproved", "edition-3", "work-1")
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("catalogue", "hardcover", (catalogue,), True),
                ("search", "open_library", (search,), True),
                ("unproved", "google_books", (unproved,), False),
            )
        )
    )

    unproved_references = {claim.reference for claim in unproved.claims}
    assert all(preview.outcome == "projected" for preview in fields.values())
    assert all(
        not (set(fields[field].source_evidence_references) & unproved_references)
        for field in ("description_fill", "tags")
    )
    assert all(
        set(fields[field].source_evidence_references) & unproved_references
        for field in ("series", "genres")
    )


@pytest.mark.parametrize(
    ("candidates", "with_lane"),
    (
        ((_target_work_candidate("catalogue", "edition-1", "work-1"),), False),
        (
            (
                _target_work_candidate("catalogue", "edition-1", "work-1"),
                _target_work_candidate("catalogue", "edition-2", "work-2"),
            ),
            True,
        ),
        (
            (
                replace(
                    _target_work_candidate("catalogue", "edition-1", "work-1"),
                    claims=tuple(
                        claim
                        for claim in _target_work_candidate(
                            "catalogue", "edition-1", "work-1"
                        ).claims
                        if claim.kind is not ClaimKind.CREATOR
                    ),
                ),
            ),
            True,
        ),
        (
            (
                replace(
                    _target_work_candidate("catalogue", "edition-1", "work-1"),
                    claims=(
                        *_target_work_candidate(
                            "catalogue", "edition-1", "work-1"
                        ).claims,
                        _claim(
                            "catalogue",
                            "edition-1",
                            ClaimKind.TITLE,
                            "Slough House Collection",
                            scope=ClaimScope.WORK,
                            order=6,
                        ),
                    ),
                ),
            ),
            True,
        ),
    ),
    ids=(
        "no_lane",
        "conflicting_work_identity",
        "insufficient_work_evidence",
        "enclosing_work",
    ),
)
def test_unproved_work_blocks_automatic_work_scoped_fields(
    candidates: tuple[ProviderCandidate, ...],
    with_lane: bool,
) -> None:
    proposal, fields = _target_work_preview(
        _target_work_report((("catalogue", "hardcover", candidates, with_lane),))
    )

    tags = next(
        result
        for result in proposal.results
        if result.destination is TargetDestination.TAGS
    )
    assert tags.alternatives
    assert fields["description_fill"].outcome == "abstain"
    assert fields["description_fill"].reason == "target_work_applicability_unproved"
    assert fields["genres"].outcome == "projected"
    assert fields["series"].reason != "target_work_applicability_unproved"
    assert (
        fields["tags"].outcome,
        fields["tags"].reason,
    ) == (
        "abstain",
        "target_work_applicability_unproved",
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
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["subtitle"] = "ask"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Split", options, destinations),
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
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["subtitle"] = "ask"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Bounded split", options, destinations),
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


def _automatic_subtitle_fill_preview(local_claims: tuple):
    report = _bibliographic_report(
        current_title="Secret History",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),
        ),
        local_claims=local_claims,
    )
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    return next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )


def test_automatic_subtitle_fill_requires_local_corroboration() -> None:
    selected = _automatic_subtitle_fill_preview(())
    assert ("abstain", "subtitle_local_corroboration_required", None) == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


def test_automatic_subtitle_fill_requires_a_provider_backed_candidate() -> None:
    report = _bibliographic_report(
        current_title="Secret History",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
            ),
        ),
        local_claims=(_local_subtitle("A Mistborn Novella", ClaimScope.WORK),),
    )
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    selected = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )
    assert ("abstain", "subtitle_local_corroboration_required", None) == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


def test_automatic_subtitle_fill_projects_the_locally_attested_value() -> None:
    corroborator = _local_subtitle("A Mistborn Novella", ClaimScope.WORK)
    selected = _automatic_subtitle_fill_preview((corroborator,))
    assert ("projected", "A Mistborn Novella") == (
        selected.outcome,
        selected.desired_value,
    )
    assert corroborator.reference in selected.source_evidence_references
    assert len(selected.source_evidence_references) > 1


def test_title_derived_colon_subtitle_stays_an_owner_proposal_not_automatic() -> None:
    title = "Secret History: A Mistborn Novella"
    local_title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=title,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )
    report = _bibliographic_report(
        current_title="Secret History",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),
        ),
        local_claims=(local_title,),
    )
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    subtitle = next(
        result
        for result in proposal.results
        if result.destination is TargetDestination.SUBTITLE
    )
    selected = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )

    assert subtitle.alternatives
    assert ("abstain", "subtitle_local_corroboration_required", None) == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


def test_explicit_subtitle_attestation_preserves_f20_authority_over_colon_signal() -> (
    None
):
    title = "Secret History: A Mistborn Novella"
    local_title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=title,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )
    local_subtitle = _local_subtitle("A Mistborn Novella", ClaimScope.WORK)
    report = _bibliographic_report(
        current_title="Secret History",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),
            _source(
                "other-catalogue",
                "edition-2",
                titles=("Secret History",),
                subtitle="Other subtitle",
            ),
        ),
        local_claims=(local_title, local_subtitle),
    )
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    subtitle = next(
        result
        for result in proposal.results
        if result.destination is TargetDestination.SUBTITLE
    )
    selected = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )

    alternative = subtitle.alternatives[0]
    provider_reference = next(
        claim.reference
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.SUBTITLE and claim.value == "A Mistborn Novella"
    )
    assert alternative.scope.value == "edition"
    assert alternative.evidence_references == (provider_reference,)
    assert ("projected", "A Mistborn Novella") == (
        selected.outcome,
        selected.desired_value,
    )
    assert local_subtitle.reference in selected.source_evidence_references
    assert provider_reference in selected.source_evidence_references


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
        ProviderQueryKind.ISBN,
        ProviderStatus.CANDIDATES,
        (local.reference,),
        "9780306406157",
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
    assert ("abstain", "manifestation_verification_required", None) == (
        exact.outcome,
        exact.reason,
        exact.desired_value,
    )
    assert ("abstain", "manifestation_verification_required", None) == (
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
    assert ("abstain", "manifestation_verification_required", None) == (
        preferred.outcome,
        preferred.reason,
        preferred.desired_value,
    )


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
        covers=(_exact_edition_cover(),),
    )

    exact = _preview_field(report, "isbn_form", "preserve_exact")
    preferred = _preview_field(report, "isbn_form", "prefer_isbn13")

    assert ("projected", "0306406152", "verified_manifestation_isbn") == (
        exact.outcome,
        exact.desired_value,
        exact.reason,
    )
    assert ("projected", "9780306406157", "verified_manifestation_isbn") == (
        preferred.outcome,
        preferred.desired_value,
        preferred.reason,
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
        covers=(_exact_edition_cover(),),
    )
    for option in ("preserve_exact", "prefer_isbn13"):
        field = _preview_field(family_report, "isbn_form", option)
        assert ("projected", "9780306406157") == (
            field.outcome,
            field.desired_value,
        )
        assert 3 == len(field.source_evidence_references)


def test_automatic_isbn_abstains_for_an_unresolved_provider_work_identity() -> None:
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
        local_claims=(_local_isbn("0306406152", "valid_isbn_10"),),
        providers=(
            replace(
                provider,
                candidates=(
                    replace(provider.candidates[0], work_identity="unresolved"),
                ),
            ),
        ),
        covers=(_exact_edition_cover(work_identity="unresolved"),),
    )
    options = dict(MetadataPolicyProfile.builtin().options)
    options["isbn_form"] = "preserve_exact"
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["isbn_form"] = "automatic"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Automatic ISBN", options, destinations),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    field = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "isbn_form"
    )

    assert ("abstain", "manifestation_verification_required", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_isbn_preview_projects_the_selected_verified_family_among_offers() -> None:
    local = _local_isbn("0306406152", "valid_isbn_10")
    verified = _provider(
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
    offered = _provider(
        "search",
        "edition-2",
        (
            _claim(
                "search",
                "edition-2",
                ClaimKind.ISBN,
                "9780140328721",
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
        providers=(verified, offered),
        covers=(_exact_edition_cover(),),
    )

    for option, value in (
        ("preserve_exact", "0306406152"),
        ("prefer_isbn13", "9780306406157"),
    ):
        preview = _preview_field(report, "isbn_form", option)
        assert ("projected", value, "verified_manifestation_isbn") == (
            preview.outcome,
            preview.desired_value,
            preview.reason,
        )
        assert preview.source_candidate_id is not None


def test_isbn_representation_uses_accepted_lexicals_within_the_elected_family() -> None:
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
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.ISBN,
                "0306406152",
                scope=ClaimScope.EDITION,
                applicability="ebook",
                order=1,
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
    candidate = _result(evaluate_report(report), TargetDestination.ISBN).alternatives[0]
    sibling = provider.candidates[0].claims[1].reference
    mixed = cast(
        MetadataReport,
        SimpleNamespace(
            local_claims=report.local_claims,
            providers=report.providers,
            identifier_evidence=tuple(
                replace(
                    evidence,
                    relation=IdentifierRelation.ASSOCIATED,
                    basis=IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
                )
                if evidence.reference == sibling
                else evidence
                for evidence in report.identifier_evidence
            ),
        ),
    )

    assert "0306406152" == select_isbn_representation(
        mixed, candidate, "preserve_exact"
    )
    assert "9780306406157" == select_isbn_representation(
        mixed, candidate, "prefer_isbn13"
    )

    without_isbn13 = cast(
        MetadataReport,
        SimpleNamespace(
            local_claims=mixed.local_claims,
            providers=mixed.providers,
            identifier_evidence=tuple(
                replace(
                    evidence,
                    relation=IdentifierRelation.ASSOCIATED,
                    basis=IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
                )
                if evidence.reference == provider.candidates[0].claims[0].reference
                else evidence
                for evidence in mixed.identifier_evidence
            ),
        ),
    )
    assert (
        select_isbn_representation(without_isbn13, candidate, "prefer_isbn13") is None
    )


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
        assert ("abstain", "manifestation_verification_required", None) == (
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
    assert ("abstain", "manifestation_verification_required", None) == (
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
