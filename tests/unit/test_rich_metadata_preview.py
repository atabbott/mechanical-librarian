"""Direct contract proof for the no-effect rich metadata preview compiler."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.cover_evidence import CoverSourceState
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
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
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
    project_audiobook_automatic_decision_value,
    project_f20_automatic_decision_value,
    select_isbn_representation,
)
from tests.support.audiobook_reports import synthetic_audiobook_product_lookup_report
from tests.support.audiobook_synopsis import synopsis_report
from tests.support.description_advice import _description_candidate
from tests.support.genre_reports import audiobook_genre_report
from tests.support.proposal_evidence import (
    _claim,
    _exact_edition_cover,
    _fields,
    _local_description,
    _local_isbn,
    _local_subtitle,
    _provider,
    _report,
    _result,
    _target_work_candidate,
    _target_work_report,
)
from tests.support.provider_recovery_rail import (
    _automatic_series_fields,
    _automatic_series_report,
    _series_claims,
    _series_provider,
)
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source

_QUALIFIED_DESCRIPTION = (
    "This complete provider description contains enough specific words to clear "
    "the conservative quality floor while remaining ordinary readable book prose."
)


@pytest.fixture
def regional_synopsis_products():
    return tuple(
        {
            "asin": f"B00000000{index}",
            "runtimeLengthMin": 970,
            "title": "Synthetic Journey",
            "authors": [{"name": "Example Author"}],
            "summary": text,
        }
        for index, text in (
            (1, "A voyage reveals a hidden city."),
            (2, "Two explorers discover an ancient city beneath the sea."),
        )
    )


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("swap_texts", (False, True))
def test_regional_synopsis_uses_stable_source_record_without_ranking_text(
    regional_synopsis_products,
    monkeypatch,
    reverse,
    swap_texts,
):
    products = regional_synopsis_products
    if swap_texts:
        products[0]["summary"], products[1]["summary"] = (
            products[1]["summary"],
            products[0]["summary"],
        )
    expected = products[0]["summary"]
    report = synthetic_audiobook_product_lookup_report(
        tuple(reversed(products)) if reverse else products,
        recording_runtime="58200.000",
    )
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
        | {"description_fill:safe_plain_text:audiobook_description_fill"},
    )
    proposal, fields = _target_work_preview(report)
    field = fields["description_fill"]
    assert field.outcome == "projected" and field.reason is None
    assert field.desired_value == expected
    winner = next(
        candidate
        for provider in report.providers
        for candidate in provider.candidates
        if candidate.identity == "audnexus-book-B000000001"
    )
    assert field.source_evidence_references == tuple(
        claim.reference
        for claim in winner.claims
        if claim.kind is ClaimKind.DESCRIPTION
    )
    assert {
        alternative.value.value
        for alternative in _description_result(proposal).alternatives
    } == {product["summary"] for product in products}
    reopened = MetadataReport.from_json(report.to_json())
    assert _target_work_preview(reopened)[1]["description_fill"] == field


@pytest.mark.parametrize(
    ("shape", "value", "reason"),
    [
        ("unsafe", "<script>bad()</script>", "description_quality_floor_not_met"),
        ("malformed", {}, "description_quality_floor_not_met"),
        ("cutoff", "A journey...", "description_quality_floor_not_met"),
        ("wrong_work", None, "no_candidate"),
    ],
)
def test_regional_synopsis_preserves_source_safety_and_work_refusals(
    regional_synopsis_products,
    monkeypatch,
    shape,
    value,
    reason,
):
    products = regional_synopsis_products
    if shape == "wrong_work":
        for product in products:
            product["title"] = "Different Work"
    else:
        products[0]["summary"] = value
    report = synthetic_audiobook_product_lookup_report(
        products, recording_runtime="58200.000"
    )
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
        | {"description_fill:safe_plain_text:audiobook_description_fill"},
    )
    proposal, fields = _target_work_preview(report)
    field = fields["description_fill"]
    assert (field.outcome, field.reason, field.desired_value) == (
        "abstain",
        reason,
        None,
    )
    # A fed answer naming another work keeps only its ASIN (#1257), so it
    # offers no description at all rather than an unproved one.
    assert bool(_description_result(proposal).alternatives) is (shape != "wrong_work")


def test_regional_synopsis_refuses_competing_texts_within_one_source_record(
    regional_synopsis_products,
    monkeypatch,
):
    report = synthetic_audiobook_product_lookup_report(
        regional_synopsis_products, recording_runtime="58200.000"
    )
    provider = next(value for value in report.providers if value.provider == "audnexus")
    candidate = provider.candidates[0]
    original = next(
        claim for claim in candidate.claims if claim.kind is ClaimKind.DESCRIPTION
    )
    competing = ProviderClaim.create(
        provider=provider.provider,
        candidate=candidate.identity,
        kind=ClaimKind.DESCRIPTION,
        value="A different account supplied inside the same source record.",
        state=FieldState.VALID,
        scope=original.scope,
        applicability=original.applicability,
        raw_order=max(claim.raw_order for claim in candidate.claims) + 1,
    )
    changed = replace(candidate, claims=(*candidate.claims, competing))
    report = MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        audio=report.audio,
        providers=tuple(
            replace(
                value,
                candidates=tuple(
                    changed if item.identity == candidate.identity else item
                    for item in value.candidates
                ),
            )
            if value.provider == provider.provider
            else value
            for value in report.providers
        ),
    )
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
        | {"description_fill:safe_plain_text:audiobook_description_fill"},
    )
    proposal, fields = _target_work_preview(report)
    assert fields["description_fill"].reason == "ambiguous_evidence"
    assert fields["description_fill"].desired_value is None
    assert len(_description_result(proposal).alternatives) == 3


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


def _series_source_bundle(provider, identity, memberships):
    return tuple(
        ProviderClaim.create(
            provider=provider,
            candidate=identity,
            kind=kind,
            value=value,
            state=FieldState.ABSENT if value is None else FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            raw_order=index * 2 + slot,
        )
        for index, (name, sequence) in enumerate(memberships)
        for slot, (kind, value) in enumerate(
            ((ClaimKind.SERIES, name), (ClaimKind.SERIES_SEQUENCE, sequence))
        )
    )


def test_series_elects_existing_superset_with_exact_order_values_and_own_evidence():
    claims = _series_source_bundle(
        "catalogue", "edition", (("Umbrella", "7"), ("Reading Order", "1"))
    )
    report = _report(
        fields=_fields(
            series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING)
        ),
        local_claims=(
            _local_isbn("9780306406157", "valid_isbn_13"),
            *_local_series_claims(sequence="1.0"),
        ),
        providers=(_series_provider("catalogue", "edition", "hardcover", claims),),
    )

    result = _result(evaluate_report(report), TargetDestination.SERIES)

    assert result.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert len(result.alternatives) == 1
    selected = result.alternatives[0]
    assert selected.value.to_data() == {
        "kind": "series",
        "value": [
            {"name": "Umbrella", "sequence": "7"},
            {"name": "Reading Order", "sequence": "1"},
        ],
    }
    assert set(selected.evidence_references) == {claim.reference for claim in claims}
    assert len(selected.correlations) == 1


@pytest.mark.parametrize(
    ("smaller", "larger", "elect"),
    (
        (
            (("Reading Order", "0.70"),),
            (("Reading Order", "0.7"), ("Umbrella", None)),
            True,
        ),
        ((("Reading Order", None),), (("Reading Order", "1"),), True),
        (
            (("Reading Order", None),),
            (("Reading Order", None), ("Umbrella", "3")),
            True,
        ),
        (
            (("Reading Order", "1"),),
            (("Reading Order", None), ("Umbrella", "3")),
            False,
        ),
        ((("Reading Order", "1"),), (("Reading Order", "2"), ("Umbrella", "3")), False),
        (
            (("Reading Order", "1e0"),),
            (("Reading Order", "1"), ("Umbrella", "3")),
            False,
        ),
        (
            (("Reading Order", "01"),),
            (("Reading Order", "1"), ("Umbrella", "3")),
            False,
        ),
        ((("Reading Order", "I"),), (("Reading Order", "1"), ("Umbrella", "3")), False),
        ((("Reading Order", "1.0"),), (("Reading Order", "1"),), False),
        ((("Reading Order", "1"),), (("Other Order", "1"),), False),
        (
            (("Reading Order", "1"), ("One", "2")),
            (("Reading Order", "1"), ("Two", "2")),
            False,
        ),
        (
            (("Reading Order", "1"), ("Umbrella", "2")),
            (("Umbrella", "2"), ("Reading Order", "1")),
            False,
        ),
    ),
)
def test_series_bundle_dominance_is_unique_complete_and_position_compatible(
    smaller, larger, elect
):
    report = _automatic_series_report(
        _series_provider(
            "first", "a", "hardcover", _series_source_bundle("first", "a", smaller)
        ),
        _series_provider(
            "second", "b", "open_library", _series_source_bundle("second", "b", larger)
        ),
    )
    result = _result(evaluate_report(report), TargetDestination.SERIES)

    assert result.outcome is (
        ProposalOutcome.PROPOSE_CHANGE if elect else ProposalOutcome.UNRESOLVED_CHOICE
    )
    assert len(result.alternatives) == (1 if elect else 2)
    if elect:
        assert [
            (item.name, item.sequence) for item in result.alternatives[0].value.value
        ] == list(larger)


def _local_series_claims(name="Reading Order", sequence="2"):
    return (
        LocalClaim.create(
            kind=ClaimKind.SERIES,
            observed=name,
            canonical=name,
            excerpt=name,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        ),
        LocalClaim.create(
            kind=ClaimKind.SERIES_SEQUENCE,
            observed=sequence,
            canonical=sequence,
            excerpt=sequence,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            occurrence=1,
        ),
    )


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize("evidence", ("candidate", "conflict", "absent"))
def test_automatic_series_preserves_populated_target_before_evidence(
    option: str, evidence: str
) -> None:
    providers = (
        ()
        if evidence == "absent"
        else (_series_provider("source-a", "a", "hardcover"),)
    )
    if evidence == "conflict":
        providers += (
            _series_provider(
                "source-b",
                "b",
                "open_library",
                _series_claims("source-b", "b", sequence="3"),
            ),
        )
    report = _report(
        local_claims=(_local_isbn("9780306406157", "valid_isbn_13"),),
        fields=_fields(
            series=TargetField(
                TargetDestination.SERIES,
                TargetFieldState.VALUE,
                (TargetSeries("Owner's reading order", "7"),),
            )
        ),
        providers=providers,
    )
    field = _automatic_series_preview(report, option)
    assert field.outcome == "no_op"
    assert field.reason == "target_preserved"
    assert field.desired_value == ({"name": "Owner's reading order", "sequence": "7"},)
    assert field.source_candidate_id is None
    assert field.source_evidence_references == ()
    assert _preview_field(report, "series", option).reason != "target_preserved"


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_automatic_series_projection_rejects_populated_current_value(
    option: str,
) -> None:
    candidate = {
        "kind": "series",
        "value": [{"name": "Reading Order", "sequence": "2"}],
    }
    with pytest.raises(
        RichMetadataPreviewError, match="rich automatic projection is invalid"
    ):
        project_f20_automatic_decision_value(
            "series",
            option,
            candidate,
            current_value=({"name": "Owner's reading order", "sequence": "7"},),
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


@pytest.mark.parametrize(
    ("incumbent", "outcome", "reason"),
    (
        (
            "<p>A librarian follows a lost expedition.</p>",
            "abstain",
            "target_state_unusable",
        ),
        (
            "A librarian follows a lost expedition.\u2028The archive holds a secret.",
            "no_op",
            "target_preserved",
        ),
        # A markup-only incumbent is missing knowledge under the fill-only
        # option too (#1317), so it reaches election: two synopses are a choice.
        ("<p></p>", "abstain", "ambiguous_evidence"),
    ),
    ids=("markup", "line_separator", "markup_only_under_fill_only"),
)
def test_admitted_markup_incumbent_keeps_no_automatic_authority(
    incumbent: str, outcome: str, reason: str
) -> None:
    # Tag-bearing incumbents read as values (#1251) and are still never plain
    # synopses; one with U+2028 is an ordinary plain synopsis.
    field = _target_work_preview(synopsis_report(incumbent))[1]["description_fill"]
    assert (field.target_state, field.outcome, field.reason) == (
        "value",
        outcome,
        reason,
    )
    assert field.current_value == incumbent


def _description_result(proposal: ProposalSet) -> ProposalResult:
    return next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.DESCRIPTION
    )


def test_equivalent_local_description_keeps_the_exact_provider_representation() -> None:
    candidate = _description_candidate(
        "catalogue", "edition-1", "work-1", "A qualified description."
    )
    local = _local_description("A QUALIFIED DESCRIPTION.")

    proposal, fields = _target_work_preview(
        _target_work_report(
            (("catalogue", "hardcover", (candidate,), True),),
            additional_local_claims=(local,),
        )
    )

    result = _description_result(proposal)
    assert len(result.alternatives) == 1
    alternative = result.alternatives[0]
    assert alternative.value.value == "A qualified description."
    assert set(alternative.evidence_references) == {
        local.reference,
        candidate.claims[3].reference,
    }
    assert fields["description_fill"].to_data() == {
        "current_value": None,
        "desired_value": "A qualified description.",
        "field": "description_fill",
        "narrowed_for_target": False,
        "option": "safe_plain_text",
        "outcome": "projected",
        "qualified_value": None,
        "reason": None,
        "target_state": "missing",
    }
    assert fields["description_fill"].source_candidate_id == alternative.alternative_id
    assert fields["description_fill"].source_combination_ids == ()
    assert fields["description_fill"].source_evidence_references == (
        candidate.claims[3].reference,
    )


def test_equivalent_provider_representations_use_canonical_order_independent_of_source_order() -> (
    None
):
    uppercase = _description_candidate(
        "alpha", "edition-1", "work-1", "A QUALIFIED DESCRIPTION."
    )
    lowercase = _description_candidate(
        "zulu", "edition-2", "work-1", "A qualified description."
    )
    transformed = replace(
        _description_candidate(
            "middle", "edition-3", "work-1", "a qualified description."
        ),
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                "google-books-html-visible-text-v1",
                ClaimScope.WORK,
            ),
        ),
    )
    local_claims = (
        _local_description("A  qualified description.", occurrence=0),
        _local_description("a qualified description.", occurrence=1),
    )
    sources = (
        ("alpha", "open_library", (uppercase,), True),
        ("middle", "google_books", (transformed,), True),
        ("zulu", "hardcover", (lowercase,), True),
    )
    observed = []
    for providers, local in (
        (sources, local_claims),
        (tuple(reversed(sources)), tuple(reversed(local_claims))),
    ):
        proposal, fields = _target_work_preview(
            _target_work_report(
                providers,
                additional_local_claims=local,
            )
        )
        result = _description_result(proposal)
        preview = fields["description_fill"]
        observed.append(
            (
                result.to_data(),
                preview.to_data(),
                preview.source_candidate_id,
                preview.source_evidence_references,
            )
        )

    assert observed[0] == observed[1]
    result_data, preview_data, source_candidate_id, source_references = observed[0]
    assert result_data["alternatives"][0]["value"]["value"] == (
        "A QUALIFIED DESCRIPTION."
    )
    assert preview_data["desired_value"] == "A QUALIFIED DESCRIPTION."
    assert source_candidate_id == result_data["alternatives"][0]["alternative_id"]
    assert source_references == (uppercase.claims[3].reference,)


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


@pytest.mark.parametrize(
    ("identity", "expected_outcome", "expected_reason"),
    (
        ("applicable", "projected", None),
        ("display_variant", "projected", None),
        ("inverted", "projected", None),
        ("malformed_display", "abstain", "target_work_contradicted"),
        ("contradictory", "abstain", "target_work_contradicted"),
        ("unavailable", "projected", None),
    ),
)
def test_automatic_genre_rejects_only_explicit_work_contradiction(
    identity: str,
    expected_outcome: str,
    expected_reason: str | None,
) -> None:
    candidate = _target_work_candidate("catalogue", "edition-1", "work-1")
    identity_claims = (
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.TITLE,
                "Different Book" if identity == "contradictory" else "Real Tigers",
                scope=ClaimScope.WORK,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                (
                    "Other Author"
                    if identity == "contradictory"
                    else ". B. Herron"
                    if identity == "malformed_display"
                    else "Herron, Mick"
                    if identity == "inverted"
                    else "Mick J Herron"
                    if identity == "display_variant"
                    else "Mick Herron"
                ),
                scope=ClaimScope.WORK,
                role="author",
            ),
        )
        if identity != "unavailable"
        else ()
    )
    candidate = replace(
        candidate,
        claims=(
            *identity_claims,
            *(
                claim
                for claim in candidate.claims
                if claim.kind not in {ClaimKind.TITLE, ClaimKind.CREATOR}
            ),
        ),
    )
    report = _target_work_report(
        (("catalogue", "hardcover", (candidate,), True),),
        target_authors=(". Herron",)
        if identity == "malformed_display"
        else ("Mick Herron",),
    )

    _, fields = _target_work_preview(report)

    assert expected_outcome == fields["genres"].outcome
    assert expected_reason == fields["genres"].reason
    assert any(
        claim.kind is ClaimKind.GENRE
        for claim in report.providers[0].candidates[0].claims
    )
    if identity == "display_variant":
        assert {
            field: (fields[field].outcome, fields[field].reason)
            for field in ("description_fill", "tags")
        } == {
            "description_fill": ("abstain", "target_work_applicability_unproved"),
            "tags": ("abstain", "target_work_applicability_unproved"),
        }


def test_automatic_description_filters_applicability_before_floor_and_order() -> None:
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
        "One short but otherwise valid description.",
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


def test_automatic_description_filters_transformed_text_before_quality_floor() -> None:
    transformed = replace(
        _description_candidate(
            "preferred",
            "edition-1",
            "work-1",
            _QUALIFIED_DESCRIPTION + " Transformed source.",
        ),
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                "google-books-html-visible-text-v1",
                ClaimScope.WORK,
            ),
        ),
    )
    ordinary = _description_candidate(
        "fallback",
        "edition-2",
        "work-1",
        "One short but otherwise valid description.",
    )
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("preferred", "google_books", (transformed,), True),
                ("fallback", "open_library", (ordinary,), True),
            )
        )
    )

    description = fields["description_fill"]
    assert ("projected", ordinary.claims[3].value) == (
        description.outcome,
        description.desired_value,
    )
    assert description.source_evidence_references == (ordinary.claims[3].reference,)


def test_generic_transformed_description_is_retained_for_review_but_not_automatic() -> (
    None
):
    transformed = replace(
        _description_candidate(
            "catalogue",
            "edition-1",
            "work-1",
            _QUALIFIED_DESCRIPTION + " Rendered from provider markup.",
        ),
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                "html-visible-text-v1",
                ClaimScope.WORK,
            ),
        ),
    )

    proposal, fields = _target_work_preview(
        _target_work_report((("catalogue", "audible_catalog", (transformed,), True),))
    )

    alternative = _description_result(proposal).alternatives[0]
    assert alternative.value.value == transformed.claims[3].value
    assert alternative.evidence_references == (transformed.claims[3].reference,)
    assert ("abstain", "no_candidate") == (
        fields["description_fill"].outcome,
        fields["description_fill"].reason,
    )


def test_generic_transformed_description_uses_equivalent_ordinary_exact_source() -> (
    None
):
    rendered = _QUALIFIED_DESCRIPTION + " Shared source text."
    transformed = replace(
        _description_candidate("markup", "edition-1", "work-1", rendered.upper()),
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                "html-visible-text-v1",
                ClaimScope.WORK,
            ),
        ),
    )
    ordinary = _description_candidate("ordinary", "edition-2", "work-1", rendered)

    proposal, fields = _target_work_preview(
        _target_work_report(
            (
                ("markup", "audible_catalog", (transformed,), True),
                ("ordinary", "open_library", (ordinary,), True),
            )
        )
    )

    alternative = _description_result(proposal).alternatives[0]
    description = fields["description_fill"]
    assert alternative.value.value == ordinary.claims[3].value
    assert set(alternative.evidence_references) == {
        transformed.claims[3].reference,
        ordinary.claims[3].reference,
    }
    assert ("projected", ordinary.claims[3].value) == (
        description.outcome,
        description.desired_value,
    )
    assert description.source_candidate_id == alternative.alternative_id
    assert description.source_evidence_references == (ordinary.claims[3].reference,)


def test_automatic_description_excludes_unproved_source_for_one_collapsed_value() -> (
    None
):
    shared_description = _QUALIFIED_DESCRIPTION + " Shared source text."
    unproved = _description_candidate(
        "preferred", "edition-1", "work-1", shared_description.upper()
    )
    applicable = _description_candidate(
        "fallback", "edition-2", "work-1", shared_description
    )
    proposal, fields = _target_work_preview(
        _target_work_report(
            (
                ("preferred", "hardcover", (unproved,), False),
                ("fallback", "google_books", (applicable,), True),
            )
        )
    )

    description = fields["description_fill"]
    alternative = _description_result(proposal).alternatives[0]
    assert alternative.value.value == shared_description
    assert ("projected", shared_description) == (
        description.outcome,
        description.desired_value,
    )
    assert description.source_evidence_references == (applicable.claims[3].reference,)


def test_transformed_description_cannot_borrow_an_equivalent_local_value() -> None:
    transformed = replace(
        _description_candidate(
            "catalogue", "edition-1", "work-1", "A qualified description."
        ),
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                "google-books-html-visible-text-v1",
                ClaimScope.WORK,
            ),
        ),
    )
    local = _local_description("A QUALIFIED DESCRIPTION.")

    proposal, fields = _target_work_preview(
        _target_work_report(
            (("catalogue", "google_books", (transformed,), True),),
            additional_local_claims=(local,),
        )
    )

    alternative = _description_result(proposal).alternatives[0]
    assert set(alternative.evidence_references) == {
        local.reference,
        transformed.claims[3].reference,
    }
    assert ("abstain", "no_candidate") == (
        fields["description_fill"].outcome,
        fields["description_fill"].reason,
    )


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


def test_automatic_description_uses_authority_without_a_length_floor() -> None:
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
    assert ("projected", too_short.claims[3].value) == (
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


def test_automatic_description_elects_stable_equal_authority_record() -> None:
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
    assert ("projected", first.claims[3].value) == (
        description.outcome,
        description.desired_value,
    )


def test_automatic_description_abstains_from_unranked_authority_competition() -> None:
    ranked = _description_candidate(
        "ranked",
        "edition-1",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Ranked source.",
    )
    unranked = _description_candidate(
        "unranked",
        "edition-2",
        "work-1",
        _QUALIFIED_DESCRIPTION + " Unranked source.",
    )
    _, fields = _target_work_preview(
        _target_work_report(
            (
                ("ranked", "open_library", (ranked,), True),
                ("unranked", "audnexus", (unranked,), True),
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
    (_QUALIFIED_DESCRIPTION + " This source visibly cuts off...",),
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


_EBOOK_PRAISE = (
    '"A triumph of quiet suspense." \u2014 Example Review '
    '"I could not put it down." \u2014 Sample Times'
)


@pytest.mark.parametrize(
    ("texts", "outcome", "reason"),
    (
        ((_EBOOK_PRAISE,), "abstain", "description_quality_floor_not_met"),
        (
            ("Praise for Synthetic Journey. " + _QUALIFIED_DESCRIPTION,),
            "abstain",
            "description_quality_floor_not_met",
        ),
        # One pull quote before a longer synopsis is a synopsis.
        (
            ('"Luminous." \u2014 Example Review. ' + _QUALIFIED_DESCRIPTION,),
            "projected",
            None,
        ),
        # A praise run beside a synopsis leaves the synopsis to elect.
        ((_EBOOK_PRAISE, _QUALIFIED_DESCRIPTION), "projected", None),
    ),
    ids=("praise_only", "praise_for", "pull_quote", "praise_beside_synopsis"),
)
def test_automatic_description_never_elects_mostly_quoted_praise(
    texts: tuple[str, ...], outcome: str, reason: str | None
) -> None:
    proposal, fields = _target_work_preview(
        _target_work_report(
            tuple(
                (
                    name,
                    "hardcover",
                    (_description_candidate(name, f"edition-{name}", "work-1", text),),
                    True,
                )
                for name, text in zip(
                    ("first", "second")[: len(texts)], texts, strict=True
                )
            )
        )
    )

    description = fields["description_fill"]
    assert (description.outcome, description.reason) == (outcome, reason)
    if outcome == "projected":
        assert description.desired_value == texts[-1]
    # Every text stays an alternative the owner may still choose.
    assert {
        value.value.value for value in _description_result(proposal).alternatives
    } >= set(texts)


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


def test_series_refuses_selected_unproved_sources_without_changing_other_fields() -> (
    None
):
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
    assert all(
        fields[field].outcome == "projected"
        for field in ("description_fill", "tags", "genres")
    )
    assert (fields["series"].outcome, fields["series"].reason) == (
        "abstain",
        "target_work_applicability_unproved",
    )
    assert all(
        not (set(fields[field].source_evidence_references) & unproved_references)
        for field in ("description_fill", "tags")
    )
    assert all(
        set(fields[field].source_evidence_references) & unproved_references
        for field in ("genres",)
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
    assert (fields["series"].outcome, fields["series"].reason) == (
        "abstain",
        "target_work_applicability_unproved",
    )
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


def _local_work_title(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )


def _segment_subtitle_preview(providers: tuple, local_claims: tuple = ()):
    report = _bibliographic_report(
        current_title="Secret History",
        providers=providers,
        local_claims=local_claims,
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
    return subtitle, selected


def _stating_provider(name: str = "open_library"):
    return _source(
        name,
        f"{name}-edition",
        titles=("Secret History",),
        subtitle="A Mistborn Novella",
    )


def _decorating_provider(name: str):
    return _source(
        name,
        f"{name}-edition",
        titles=("Secret History: A Mistborn Novella",),
    )


def test_cross_source_title_segment_corroborates_an_automatic_subtitle_fill() -> None:
    subtitle, selected = _segment_subtitle_preview(
        (_stating_provider(), _decorating_provider("hardcover"))
    )

    assert subtitle.alternatives
    assert ("projected", None, "A Mistborn Novella") == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


def test_a_local_only_title_segment_withholds_the_automatic_fill() -> None:
    _, selected = _segment_subtitle_preview(
        (_stating_provider(),),
        (_local_work_title("Secret History: A Mistborn Novella"),),
    )

    assert ("abstain", "subtitle_segment_decorated_consensus", None) == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


def test_more_decorating_authorities_than_plain_withhold_the_automatic_fill() -> None:
    _, selected = _segment_subtitle_preview(
        (
            _stating_provider(),
            _decorating_provider("hardcover"),
            _decorating_provider("google_books"),
        )
    )

    assert ("abstain", "subtitle_segment_decorated_consensus", None) == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


def test_a_dissenting_provider_subtitle_withholds_the_automatic_fill() -> None:
    dissenting = _provider(
        "google_books",
        "google_books-print",
        (
            _claim(
                "google_books",
                "google_books-print",
                ClaimKind.TITLE,
                "Secret History",
                scope=ClaimScope.WORK,
            ),
            _claim(
                "google_books",
                "google_books-print",
                ClaimKind.SUBTITLE,
                "A Cosmere Novella",
                scope=ClaimScope.EDITION,
                order=1,
                applicability="print",
            ),
        ),
        authority="google_books",
    )
    _, selected = _segment_subtitle_preview(
        (_stating_provider(), _decorating_provider("hardcover"), dissenting)
    )

    assert ("abstain", "subtitle_segment_provider_dissent", None) == (
        selected.outcome,
        selected.reason,
        selected.desired_value,
    )


def test_a_generic_segment_never_reaches_the_automatic_fill() -> None:
    subtitle, selected = _segment_subtitle_preview(
        (
            _source(
                "open_library",
                "open_library-edition",
                titles=("Secret History",),
                subtitle="Stories",
            ),
            _source(
                "hardcover",
                "hardcover-edition",
                titles=("Secret History: Stories",),
            ),
        )
    )

    assert ProposalOutcome.INELIGIBLE is subtitle.outcome
    assert ("generic_provider_subtitle",) == subtitle.reasons
    assert ("abstain", None) == (selected.outcome, selected.desired_value)


def test_own_source_title_segment_never_corroborates_its_own_subtitle() -> None:
    report = _bibliographic_report(
        current_title="Secret History",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History: A Mistborn Novella",),
                subtitle="A Mistborn Novella",
            ),
            _source("archive", "edition-2", titles=("Secret History",)),
        ),
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


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_automatic_series_preserves_absent_membership_position(option) -> None:
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
    field = _automatic_series_preview(report, option)

    assert ProposalOutcome.PROPOSE_CHANGE is manual.outcome
    assert ("projected", None, ({"name": "Reading Order", "sequence": None},)) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_automatic_series_ignores_an_unrelated_provider_with_same_query_kind(option):
    provider = replace(
        _series_provider("catalogue", "edition-1", "hardcover"),
        query_kind=ProviderQueryKind.ISBN,
    )
    report = _automatic_series_report(
        provider,
        ProviderObservation(
            "other", ProviderStatus.UNAVAILABLE, query_kind=ProviderQueryKind.ISBN
        ),
    )
    field = _automatic_series_preview(report, option)
    assert (field.outcome, field.reason) == ("projected", None)


def test_automatic_series_uses_one_explicitly_positioned_matching_bundle() -> None:
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

    assert ("projected", None, ({"name": "Reading Order", "sequence": "2"},)) == (
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

    assert ("abstain", "series_conflicting_membership", None) == (
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
                    ClaimKind.GENRE,
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
                    ClaimKind.GENRE,
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


def test_automatic_series_projects_one_provider_lineage() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider("mirror", "edition-2", "hardcover"),
    )

    field = _automatic_series_preview(report)

    assert ("projected", None, ({"name": "Reading Order", "sequence": "2"},)) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_requires_retained_provider_evidence() -> None:
    report = _report(
        fields=_fields(
            series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING)
        ),
        local_claims=_local_series_claims(),
    )

    field = _automatic_series_preview(report)

    assert ("abstain", "series_provider_evidence_required", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_allows_local_and_provider_evidence() -> None:
    report = _report(
        fields=_automatic_series_fields(),
        local_claims=(
            _local_isbn("9780306406157", "valid_isbn_13"),
            *_local_series_claims(),
        ),
        providers=(_series_provider("catalogue", "edition-1", "hardcover"),),
    )

    field = _automatic_series_preview(report)

    assert "projected" == field.outcome


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


def test_automatic_series_abstains_for_generic_decoration_name_conflict() -> None:
    report = _automatic_series_report(
        _series_provider(
            "catalogue",
            "edition-1",
            "hardcover",
            _series_claims("catalogue", "edition-1", name="Series", sequence="1"),
        ),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            _series_claims("search", "edition-2", name="Trilogy", sequence="1"),
        ),
    )

    manual = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.SERIES
    )
    field = _automatic_series_preview(report)

    assert ProposalOutcome.UNRESOLVED_CHOICE is manual.outcome
    assert ("abstain", "series_conflicting_membership", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_ignores_an_unrelated_provider_failure() -> None:
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider("search", "edition-2", "open_library"),
        ProviderObservation("catalogue_backup", ProviderStatus.UNAVAILABLE),
    )

    field = _automatic_series_preview(report)

    assert ("projected", None, ({"name": "Reading Order", "sequence": "2"},)) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_ignores_an_unrelated_failed_lane() -> None:
    base = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider("search", "edition-2", "open_library"),
    )
    local = _local_isbn("9780306406157", "valid_isbn_13")
    provider = base.providers[0]
    successful_lane = provider.lanes[0]
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

    assert ("projected", None, ({"name": "Reading Order", "sequence": "2"},)) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize(
    "relation", ("same", "different_family", "different_input", "different_kind")
)
def test_automatic_series_scopes_failed_dispatch_to_contributing_provenance(
    option, relation
) -> None:
    base = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
    )
    local = _local_isbn("9780306406157", "valid_isbn_13")
    provider = base.providers[0]
    other_input = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Other query",
        canonical="Other query",
        excerpt="Other query",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    lane = provider.lanes[0]
    failed = ProviderLaneObservation(
        "lane_" + "b" * 64,
        ProviderQueryKind.WORK
        if relation == "different_kind"
        else ProviderQueryKind.ISBN,
        ProviderStatus.UNAVAILABLE,
        (other_input.reference if relation == "different_input" else local.reference,),
        None
        if relation == "different_kind"
        else "9780140328721"
        if relation == "different_family"
        else "9780306406157",
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=(local, other_input),
        local_reasons=("no_opening_matter",),
        providers=(
            replace(
                provider,
                input_references=tuple(
                    sorted(set(lane.input_references + failed.input_references))
                ),
                query_kind=ProviderQueryKind.DISCOVERY,
                lanes=(lane, failed),
            ),
        ),
    )

    field = _automatic_series_preview(report, option)

    expected = (
        ("abstain", "series_provider_failure_isbn", None)
        if relation == "same"
        else ("projected", None, ({"name": "Reading Order", "sequence": "2"},))
    )
    assert expected == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


def test_automatic_series_treats_provider_wide_unsupported_output_as_no_evidence() -> (
    None
):
    unsupported_series = ProviderClaim.create(
        provider="open_library",
        candidate="edition-2",
        kind=ClaimKind.SERIES,
        value=None,
        state=FieldState.UNSUPPORTED,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=0,
    )
    unsupported_sequence = ProviderClaim.create(
        provider="open_library",
        candidate="edition-2",
        kind=ClaimKind.SERIES_SEQUENCE,
        value=None,
        state=FieldState.UNSUPPORTED,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=1,
    )
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "open_library",
            "edition-2",
            "open_library",
            (unsupported_series, unsupported_sequence),
        ),
    )

    field = _automatic_series_preview(report)

    assert "projected" == field.outcome


def test_automatic_series_ignores_unselected_unsupported_duplicate_membership() -> None:
    unsupported_sequence = ProviderClaim.create(
        provider="open_library",
        candidate="edition-2",
        kind=ClaimKind.SERIES_SEQUENCE,
        value=None,
        state=FieldState.UNSUPPORTED,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=1,
    )
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "open_library",
            "edition-2",
            "open_library",
            (
                _claim(
                    "open_library",
                    "edition-2",
                    ClaimKind.SERIES,
                    "Reading Order",
                    scope=ClaimScope.WORK,
                ),
                unsupported_sequence,
            ),
        ),
    )

    field = _automatic_series_preview(report)

    assert ("projected", None, ({"name": "Reading Order", "sequence": "2"},)) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_automatic_series_abstains_when_membership_is_materially_incomplete(
    option,
) -> None:
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

    field = _automatic_series_preview(report, option)

    assert ("abstain", "series_membership_incomplete", None) == (
        field.outcome,
        field.reason,
        field.desired_value,
    )


@pytest.mark.parametrize(
    ("kind", "state"),
    (
        (ClaimKind.SERIES, FieldState.MALFORMED),
        (ClaimKind.SERIES, FieldState.OVERSIZED),
        (ClaimKind.SERIES_SEQUENCE, FieldState.MALFORMED),
        (ClaimKind.SERIES_SEQUENCE, FieldState.OVERSIZED),
    ),
)
@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
@pytest.mark.parametrize("same_provider", (False, True))
def test_automatic_series_ignores_malformed_unselected_provider_candidate(
    kind: ClaimKind,
    state: FieldState,
    option,
    same_provider,
) -> None:
    other_provider = "catalogue" if same_provider else "open_library"
    marker = ProviderClaim.create(
        provider=other_provider,
        candidate="edition-2",
        kind=kind,
        value=None,
        state=state,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=0,
    )
    selected = _series_provider("catalogue", "edition-1", "hardcover")
    unselected = _series_provider(
        other_provider, "edition-2", "open_library", (marker,)
    )
    report = _automatic_series_report(
        *(
            (replace(selected, candidates=selected.candidates + unselected.candidates),)
            if same_provider
            else (selected, unselected)
        )
    )

    field = _automatic_series_preview(report, option)

    assert ("projected", None, ({"name": "Reading Order", "sequence": "2"},)) == (
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


@pytest.mark.parametrize(
    "current", ("Owner description", " \tOwner\r\ndescription.\t ")
)
@pytest.mark.parametrize("option", ("google_visible_text", "safe_plain_text"))
def test_description_preserves_populated_and_abstains_on_ambiguity_or_absence(
    current: str,
    option: str,
) -> None:
    populated = _report(
        fields=_fields(
            description=TargetField(
                TargetDestination.DESCRIPTION,
                TargetFieldState.VALUE,
                current,
            )
        )
    )
    preserved = _preview_field(populated, "description_fill", option)
    assert ("no_op", current, "target_preserved") == (
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


@pytest.mark.parametrize(
    "state", (TargetFieldState.MISSING, TargetFieldState.NULL, TargetFieldState.EMPTY)
)
@pytest.mark.parametrize("image_available", (True, False))
def test_isbn_family_preview_selects_exact_local_or_isbn13_representation(
    state: TargetFieldState, image_available: bool
) -> None:
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
        fields=_fields(isbn=TargetField(TargetDestination.ISBN, state)),
        local_claims=(local,),
        providers=(provider,),
        covers=(
            _exact_edition_cover()
            if image_available
            else replace(
                _exact_edition_cover(),
                state=CoverSourceState.UNAVAILABLE,
                artifact=None,
            ),
        ),
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
        fields=_fields(isbn=TargetField(TargetDestination.ISBN, state)),
        local_claims=(local13,),
        providers=(family_provider,),
        covers=(
            _exact_edition_cover()
            if image_available
            else replace(
                _exact_edition_cover(),
                state=CoverSourceState.UNAVAILABLE,
                artifact=None,
            ),
        ),
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
            audio=None,
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
            audio=None,
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


@pytest.mark.parametrize(
    "state", (TargetFieldState.MISSING, TargetFieldState.NULL, TargetFieldState.EMPTY)
)
@pytest.mark.parametrize("supported", (False, True))
def test_retained_decoration_subtitle_requires_independent_support(state, supported):
    from tests.support.proposal_evidence import retained_decoration_report

    report = retained_decoration_report("subtitle", state=state, supported=supported)
    options = dict(MetadataPolicyProfile.builtin().options)
    options["subtitle"] = "fill_empty"
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["subtitle"] = "automatic"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Retained subtitle", options, destinations),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    field = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )
    if supported:
        assert field.outcome == "projected"
        assert field.desired_value == "A Second Look"
        assert len(proposal.bibliographic_combinations) == 1
    else:
        assert field.outcome == "abstain"
        assert field.qualified_value is None
        assert next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.SUBTITLE
        ).alternatives


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_retained_decoration_series_preserves_qualified_projection(option):
    from tests.support.proposal_evidence import retained_decoration_report

    field = _automatic_series_preview(retained_decoration_report("series"), option)
    assert field.outcome == "projected"
    assert field.desired_value == ({"name": "The Sequence", "sequence": None},)


@pytest.mark.parametrize("field", ("genres", "tags"))
def test_ebook_add_keeps_its_existing_exact_membership_comparison(field):
    preview = _list_preview(field, "add", ("FANTASY", "EPIC"))
    assert preview.outcome == "projected"
    assert preview.desired_value == ("FANTASY", "EPIC", "Fantasy", "Epic")
    assert project_f20_automatic_decision_value(
        field,
        "add",
        {"kind": "list", "value": ["Fantasy", "Epic"]},
        ["FANTASY", "EPIC"],
    ) == {"kind": "list", "value": ["FANTASY", "EPIC", "Fantasy", "Epic"]}


def test_ebook_same_record_conflict_precedes_placeholder_fitness():
    candidate = _description_candidate(
        "catalogue", "edition-1", "work-1", "(duplicate)"
    )
    competing = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "A voyage begins.",
        scope=ClaimScope.WORK,
        order=99,
    )
    candidate = replace(candidate, claims=(*candidate.claims, competing))
    _, fields = _target_work_preview(
        _target_work_report((("catalogue", "hardcover", (candidate,), True),))
    )
    assert (fields["description_fill"].outcome, fields["description_fill"].reason) == (
        "abstain",
        "ambiguous_evidence",
    )


@pytest.mark.parametrize(
    "summaries",
    [
        ("(duplicate)",),
        ("294 pages ; 22 cm",),
        ("(Duplicate)", "(duplicate)"),
        ("294 pages;22 cm", "(duplicate)"),
    ],
)
def test_ebook_placeholder_synopses_abstain(summaries):
    from tests.support.ebook_synopsis import synopsis_report

    _, fields = _target_work_preview(synopsis_report(summaries=summaries))
    field = fields["description_fill"]
    assert (field.outcome, field.reason) == (
        "abstain",
        "description_quality_floor_not_met",
    )
    assert field.desired_value is None
    assert field.source_evidence_references == ()


@pytest.mark.parametrize(
    "summaries,winner_index",
    [
        (("(duplicate)", "A voyage begins."), 1),
        (("294 pages ; 22 cm", "A voyage begins."), 1),
        (("A voyage begins.", "(duplicate)"), 0),
    ],
)
def test_ebook_mixed_synopses_select_useful_source(summaries, winner_index):
    from tests.support.ebook_synopsis import synopsis_report

    report = synopsis_report(summaries=summaries)
    _, fields = _target_work_preview(report)
    field = fields["description_fill"]
    assert (field.outcome, field.reason) == ("projected", None)
    assert field.desired_value == "A voyage begins."
    winner = report.providers[winner_index].candidates[0]
    assert field.source_evidence_references == tuple(
        claim.reference
        for claim in winner.claims
        if claim.kind is ClaimKind.DESCRIPTION
    )


@pytest.mark.parametrize(
    ("current", "outcome", "reason", "desired"),
    (
        (("Audiobook",), "projected", None, ("Fantasy",)),
        (("Unabridged", "(Audio Book)"), "projected", None, ("Fantasy",)),
        (
            ("Audiobook", "Fantasy"),
            "no_op",
            "target_preserved",
            ("Audiobook", "Fantasy"),
        ),
        (("Science Fiction",), "no_op", "target_preserved", ("Science Fiction",)),
    ),
)
def test_audiobook_format_only_genres_are_filled_under_fill_empty(
    current, outcome, reason, desired
) -> None:
    """A list naming only the recording's format is empty-equivalent (#1250)."""
    _, fields = _target_work_preview(audiobook_genre_report(current, ("Fantasy",)))

    genres = fields["genres"]
    assert (genres.outcome, genres.reason, genres.desired_value) == (
        outcome,
        reason,
        desired,
    )


@pytest.mark.parametrize(
    ("current", "candidate"),
    ((None, ("Audiobook",)), (("Audiobook",), ("Unabridged", "Audiobooks"))),
)
def test_a_format_only_audiobook_genre_candidate_is_no_candidate(
    current, candidate
) -> None:
    _, fields = _target_work_preview(audiobook_genre_report(current, candidate))

    genres = fields["genres"]
    assert (genres.outcome, genres.reason, genres.desired_value) == (
        "abstain",
        "no_candidate",
        None,
    )


@pytest.mark.parametrize(
    "pair", ((("Audiobook",), ("Fantasy",)), (("Fantasy",), ("Unabridged",)))
)
def test_a_format_only_genre_alternative_does_not_make_a_real_one_ambiguous(
    pair,
) -> None:
    """A format-only list is no candidate, so it cannot compete (#1250)."""
    _, fields = _target_work_preview(audiobook_genre_report(None, *pair))

    genres = fields["genres"]
    assert (genres.outcome, genres.desired_value) == ("projected", ("Fantasy",))
    assert project_audiobook_automatic_decision_value(
        "genres",
        "genres",
        "fill_empty:audiobook_genres_fill",
        {"kind": "list", "value": ["Fantasy"]},
        None,
    ) == {"kind": "list", "value": ["Fantasy"]}


def test_two_real_audiobook_genre_lists_are_still_ambiguous() -> None:
    _, fields = _target_work_preview(
        audiobook_genre_report(None, ("Fantasy",), ("Mystery",))
    )

    genres = fields["genres"]
    assert (genres.outcome, genres.reason, genres.desired_value) == (
        "abstain",
        "ambiguous_evidence",
        None,
    )


def test_an_ebook_format_only_genre_alternative_still_competes() -> None:
    from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope
    from tests.support.genre_reports import genre_report
    from tests.support.proposal_evidence import _claim, _provider

    other = _provider(
        "other-catalogue",
        "edition-2",
        (
            _claim(
                "other-catalogue",
                "edition-2",
                ClaimKind.GENRE,
                "Audiobook",
                scope=ClaimScope.WORK,
            ),
        ),
    )
    _, fields = _target_work_preview(genre_report(genres=("Fantasy",), extra=(other,)))

    genres = fields["genres"]
    assert (genres.outcome, genres.reason, genres.desired_value) == (
        "abstain",
        "ambiguous_evidence",
        None,
    )


def test_an_ebook_format_only_genre_list_is_still_preserved() -> None:
    """The empty-equivalence is the audiobook's alone."""
    preview = _list_preview("genres", "fill_empty", ("Audiobook",))

    assert (preview.outcome, preview.reason, preview.desired_value) == (
        "no_op",
        "target_preserved",
        ("Audiobook",),
    )


@pytest.mark.parametrize(
    "summary",
    (
        "Narrated by Example Narrator",
        "Unabridged - Narrated by Example Narrator",
        "Audiobook",
        "Part 1 of 3",
        "N/A",
        "Section 2",
        "Read by Example Narrator; Unabr",
        "Read by Example Narrator; Length: 10 hrs and 3 mins",
        "Audiobook created by Markable",
        "fre:ac - free audio converter",
    ),
)
def test_an_audiobook_credit_or_fragment_candidate_misses_the_floor(
    regional_synopsis_products, monkeypatch, summary
) -> None:
    products = tuple(
        {**product, "summary": summary} for product in regional_synopsis_products
    )
    report = synthetic_audiobook_product_lookup_report(
        products, recording_runtime="58200.000"
    )
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
        | {"description_fill:safe_plain_text:audiobook_description_fill"},
    )
    _, fields = _target_work_preview(report)

    field = fields["description_fill"]
    assert (field.outcome, field.reason, field.desired_value) == (
        "abstain",
        "description_quality_floor_not_met",
        None,
    )


def test_an_ebook_credit_line_candidate_is_not_held_to_the_audiobook_floor() -> None:
    from tests.support.ebook_synopsis import synopsis_report as ebook_synopsis_report

    _, fields = _target_work_preview(
        ebook_synopsis_report(summaries=("Narrated by Example Narrator",))
    )

    field = fields["description_fill"]
    assert (field.outcome, field.desired_value) == (
        "projected",
        "Narrated by Example Narrator",
    )
