"""Placeholder, cut-off, header, marketing and language rules for audiobooks (#1317)."""

from dataclasses import replace

import pytest

from mechanical_librarian.domain.audiobook_decision_qualification import (
    identity_for_effect,
)
from mechanical_librarian.domain.field_value_comparison import (
    canonical_text,
    description_extends,
    marketing_copy,
    synopsis_header,
    synopsis_missing,
    values_match,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    ProviderEvidenceKind,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAssessment,
    ProposalOutcome,
    evaluate_report,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichFieldPreview,
    RichMetadataPreviewError,
    compile_rich_metadata_preview,
    project_audiobook_automatic_decision_value,
)
from tests.support.audiobook_reports import synthetic_audiobook_product_lookup_report
from tests.support.audiobook_synopsis import recording_synopsis_report
from tests.support.genre_reports import audiobook_genre_report

_SYNOPSIS = "A voyage reveals a hidden city."
_CUT = "A voyage reveals a hid…"


def _preview(
    report: MetadataReport, field: str, option: str | None = None
) -> tuple[RichFieldPreview, object]:
    options = dict(DEFAULT_OPTIONS)
    if option is not None:
        options[field] = option
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations[field] = "automatic"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Text hygiene", options, destinations),
        report.target.library,
    )
    proposal = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    preview = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == field
    )
    return preview, proposal


def _result(proposal, destination: TargetDestination):
    return next(value for value in proposal.results if value.destination is destination)


# -- boilerplate header -------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "ABOUT THE BOOK\nA voyage reveals a hidden city.",
        "Synopsis:\r\nA voyage reveals a hidden city.",
        "  Description\n\nA voyage reveals a hidden city.",
        "Publisher’s Summary A voyage reveals a hidden city.",
    ],
)
def test_a_leading_header_line_is_read_past_when_comparing(value):
    assert synopsis_header(value)
    assert values_match(TargetDestination.DESCRIPTION, value, _SYNOPSIS)
    assert canonical_text(TargetDestination.DESCRIPTION, value) == _SYNOPSIS.casefold()


@pytest.mark.parametrize(
    "value",
    [
        "About the book: a voyage reveals a hidden city.",
        "DESCRIPTION",
        "Description\n",
        "A voyage.\nSynopsis\nA hidden city.",
        "About the books\nA voyage reveals a hidden city.",
    ],
)
def test_a_header_needs_its_own_first_line_and_text_after_it(value):
    assert not synopsis_header(value)


@pytest.mark.parametrize(
    ("option", "outcome", "desired"),
    [
        ("safe_plain_text", "no_op", "ABOUT THE BOOK\n" + _SYNOPSIS),
        ("replace_safe_plain_text", "projected", _SYNOPSIS),
    ],
)
def test_a_header_led_incumbent_is_kept_or_loses_only_its_header(
    option, outcome, desired
):
    incumbent = "ABOUT THE BOOK\n" + _SYNOPSIS
    field, proposal = _preview(
        recording_synopsis_report(incumbent, (_SYNOPSIS,)), "description_fill", option
    )
    result = _result(proposal, TargetDestination.DESCRIPTION)
    # The candidate states the incumbent's synopsis, so it preserves it.
    assert result.outcome is ProposalOutcome.KEEP
    assert result.alternatives[0].assessment is ProposalAssessment.PRESERVES_TARGET
    assert (field.outcome, field.desired_value) == (outcome, desired)
    if outcome == "projected":
        assert (
            identity_for_effect(
                "description", "description_fill", option, incumbent, _SYNOPSIS
            )
            == f"description_fill:{option}:audiobook_description_replace"
        )
        assert project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            f"{option}:audiobook_description_replace",
            {"kind": "text", "value": _SYNOPSIS},
            incumbent,
        ) == {"kind": "text", "value": _SYNOPSIS}


def test_header_removal_is_never_a_different_synopsis_or_another_option():
    incumbent = "ABOUT THE BOOK\n" + _SYNOPSIS
    with pytest.raises(RichMetadataPreviewError):
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "replace_safe_plain_text:audiobook_description_replace",
            {"kind": "text", "value": "An explorer crosses the sea."},
            incumbent,
        )
    with pytest.raises(RichMetadataPreviewError):
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "safe_plain_text:audiobook_description_replace",
            {"kind": "text", "value": _SYNOPSIS},
            incumbent,
        )


# -- cut-off text ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("cut_off", "complete", "expected"),
    [
        (_CUT, _SYNOPSIS, True),
        ("A voyage reveals...", _SYNOPSIS, True),
        ("ABOUT THE BOOK\nA voyage reveals a--", _SYNOPSIS, True),
        ("A voyage conceals a hid…", _SYNOPSIS, False),
        ("A voyage reveals a hidden city…", _SYNOPSIS, False),
        (_CUT, "A voyage reveals a hid…", False),
        ("A voyage reveals a hid", _SYNOPSIS, False),
        ("<p>A voyage reveals a hid…</p>", _SYNOPSIS, False),
        ("…", _SYNOPSIS, False),
    ],
)
def test_description_extends_reads_only_an_opening(cut_off, complete, expected):
    assert description_extends(cut_off, complete) is expected


def test_a_cut_off_source_stops_vetoing_once_another_source_completes_it():
    field, proposal = _preview(
        recording_synopsis_report(None, (_CUT, _SYNOPSIS)), "description_fill"
    )
    assert (field.outcome, field.desired_value) == ("projected", _SYNOPSIS)
    # The cut-off text stays choosable and is never elected.
    assert {
        alternative.value.value
        for alternative in _result(proposal, TargetDestination.DESCRIPTION).alternatives
    } == {_CUT, _SYNOPSIS}


def test_a_cut_off_source_no_other_source_completes_still_vetoes():
    field, _ = _preview(
        recording_synopsis_report(None, ("An explorer crosses the sea…", _SYNOPSIS)),
        "description_fill",
    )
    assert (field.outcome, field.reason) == (
        "abstain",
        "description_quality_floor_not_met",
    )


@pytest.mark.parametrize("option", ["safe_plain_text", "replace_safe_plain_text"])
def test_a_cut_off_incumbent_is_completed_by_its_extension(option):
    field, _ = _preview(
        recording_synopsis_report(_CUT, (_SYNOPSIS,)), "description_fill", option
    )
    assert (field.outcome, field.desired_value) == ("projected", _SYNOPSIS)
    assert synopsis_missing(_CUT, _SYNOPSIS)
    assert (
        identity_for_effect("description", "description_fill", option, _CUT, _SYNOPSIS)
        == f"description_fill:{option}:audiobook_description_fill"
    )
    assert project_audiobook_automatic_decision_value(
        "description",
        "description_fill",
        f"{option}:audiobook_description_fill",
        {"kind": "text", "value": _SYNOPSIS},
        _CUT,
    ) == {"kind": "text", "value": _SYNOPSIS}


def test_a_cut_off_incumbent_is_not_replaced_by_another_synopsis():
    other = "An explorer crosses the sea and discovers an ancient city."
    field, _ = _preview(recording_synopsis_report(_CUT, (other,)), "description_fill")
    assert (field.outcome, field.reason) == ("abstain", "target_state_unusable")
    assert not synopsis_missing(_CUT, other)
    with pytest.raises(RichMetadataPreviewError):
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "safe_plain_text:audiobook_description_fill",
            {"kind": "text", "value": other},
            _CUT,
        )


# -- placeholders on the default option -----------------------------------------


@pytest.mark.parametrize(
    "current", ["Unabridged", "Chapter 1", "Read by Example Narrator"]
)
def test_the_default_option_fills_over_a_placeholder_and_never_elects_one(current):
    field, proposal = _preview(
        recording_synopsis_report(current, ("Track 2", _SYNOPSIS)), "description_fill"
    )
    assert DEFAULT_OPTIONS["description_fill"] == "safe_plain_text"
    assert (field.outcome, field.desired_value) == ("projected", _SYNOPSIS)
    assert "Track 2" in {
        alternative.value.value
        for alternative in _result(proposal, TargetDestination.DESCRIPTION).alternatives
    }


def test_the_default_option_keeps_a_useful_synopsis():
    field, _ = _preview(
        recording_synopsis_report(
            "A librarian follows a lost expedition.", (_SYNOPSIS,)
        ),
        "description_fill",
    )
    assert (field.outcome, field.reason) == ("no_op", "target_preserved")


# -- marketing subtitles -------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "The compulsive thriller, now on Apple TV+",
        "The bestselling novels that inspired the hit television series",
        "The must-read Sunday Times bestseller",
        "A first contact story from the Example Prize winner",
        "An Immersive Thriller From The Author of Example Matter",
        "Award-winning science fiction",
        "Winner of the Example Award 2020",
        "Now a Major Motion Picture",
        "Over a million copies sold",
        "The #1 thriller of the year",
    ],
)
def test_marketing_copy_is_recognized(value):
    assert marketing_copy(value)


@pytest.mark.parametrize(
    "value",
    [
        "A Mistborn Novel",
        "The Example Series, Book 2",
        "Book 4 of the Example Saga",
        "Military Science Fiction Adventure",
        "Holiday Stories from Beloved Authors Across the Globe",
        "Or the Necessity of Violence: An Arcane History",
        "Stories Inspired by the Sea",
        "The Author of Lies",
        "Book #12",
    ],
)
def test_a_real_subtitle_is_not_marketing_copy(value):
    assert not marketing_copy(value)


def _subtitle_report(subtitle: str) -> MetadataReport:
    return synthetic_audiobook_product_lookup_report(
        (
            {
                "asin": "B000000001",
                "runtimeLengthMin": 970,
                "title": "Synthetic Journey",
                "subtitle": subtitle,
                "authors": [{"name": "Example Author"}],
                "narrators": [{"name": "Example Narrator"}],
                "summary": _SYNOPSIS,
            },
        ),
        recording_runtime="58200.000",
    )


@pytest.mark.parametrize(
    ("subtitle", "outcome"),
    [
        ("A Mistborn Novel", "projected"),
        ("The must-read Sunday Times bestseller", "abstain"),
    ],
)
def test_a_marketing_subtitle_is_never_filled_but_stays_choosable(subtitle, outcome):
    field, proposal = _preview(_subtitle_report(subtitle), "subtitle")
    assert field.outcome == outcome
    assert [
        alternative.value.value
        for alternative in _result(proposal, TargetDestination.SUBTITLE).alternatives
    ] == [subtitle]


def test_persisted_reconstruction_refuses_a_marketing_subtitle():
    with pytest.raises(RichMetadataPreviewError):
        project_audiobook_automatic_decision_value(
            "subtitle",
            "subtitle",
            "replace_descriptive:audiobook_subtitle_replace",
            {"kind": "text", "value": "From the author of Example Matter"},
            None,
        )


# -- language gates ------------------------------------------------------------


def _regional(
    report: MetadataReport,
    marketplaces: dict[str, str],
    *,
    local: str | None = None,
    library: tuple[str, ...] = (),
) -> MetadataReport:
    """Move named products to another marketplace and declare a language."""

    def asin(candidate) -> str | None:
        return next(
            (claim.value for claim in candidate.claims if claim.kind is ClaimKind.ASIN),
            None,
        )

    providers = tuple(
        replace(
            observation,
            candidates=tuple(
                replace(
                    candidate,
                    evidence=tuple(
                        replace(evidence, value=marketplaces[asin(candidate)])
                        if evidence.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
                        and asin(candidate) in marketplaces
                        else evidence
                        for evidence in candidate.evidence
                    ),
                )
                for candidate in observation.candidates
            ),
        )
        for observation in report.providers
    )
    claims = report.local_claims
    if local is not None:
        claims = (
            *claims,
            LocalClaim.create(
                kind=ClaimKind.LANGUAGE,
                observed=local,
                canonical=local,
                excerpt=local,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.EDITION,
                basis="embedded_tag",
                occurrence=7,
            ),
        )
    return MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=claims,
        local_reasons=report.local_reasons,
        providers=providers,
        audio=report.audio,
        library_languages=library,
    )


@pytest.mark.parametrize(
    ("local", "library", "outcome", "desired"),
    [
        # With no known language nothing is gated, so two lists are a choice.
        (None, (), "abstain", None),
        (None, ("en", "fr"), "abstain", None),
        # One declared language, or the recording's own, withholds the other.
        (None, ("en",), "projected", ("Fantasy",)),
        ("en", (), "projected", ("Fantasy",)),
        ("eng", ("fr",), "projected", ("Fantasy",)),
        ("fr", ("en",), "projected", ("Fantastique",)),
    ],
)
def test_a_genre_list_from_another_language_marketplace_is_withheld(
    local, library, outcome, desired
):
    report = _regional(
        audiobook_genre_report(None, ("Fantasy",), ("Fantastique",)),
        {"B000000002": "fr"},
        local=local,
        library=library,
    )
    field, proposal = _preview(report, "genres")
    assert (field.outcome, field.desired_value) == (outcome, desired)
    assert len(_result(proposal, TargetDestination.GENRES).alternatives) == 2


def test_a_lone_foreign_genre_list_asks_and_says_why():
    report = _regional(
        audiobook_genre_report(None, ("Fantastique",)),
        {"B000000001": "fr", "B000000002": "fr"},
        library=("en",),
    )
    field, proposal = _preview(report, "genres")
    assert (field.outcome, field.reason) == ("abstain", "candidate_language_differs")
    assert [
        alternative.value.value
        for alternative in _result(proposal, TargetDestination.GENRES).alternatives
    ] == [("Fantastique",)]


@pytest.mark.parametrize(
    ("library", "outcome"),
    [(("en",), "projected"), (("fr",), "projected"), (("de",), "abstain")],
)
def test_canada_carries_english_and_french(library, outcome):
    report = _regional(
        audiobook_genre_report(None, ("Fantasy",)),
        {"B000000001": "ca", "B000000002": "ca"},
        library=library,
    )
    assert _preview(report, "genres")[0].outcome == outcome


def test_a_declared_product_language_outside_the_item_is_withheld():
    portuguese = "Uma viagem revela uma cidade escondida."
    report = synthetic_audiobook_product_lookup_report(
        tuple(
            {
                "asin": f"B00000000{index}",
                "runtimeLengthMin": 970,
                "title": "Synthetic Journey",
                "authors": [{"name": "Example Author"}],
                "narrators": [{"name": "Example Narrator"}],
                "summary": summary,
                "language": language,
            }
            for index, (summary, language) in enumerate(
                ((portuguese, "pt"), (_SYNOPSIS, "en")), 1
            )
        ),
        recording_runtime="58200.000",
    )
    # Both resolved in the US store; only the stated language tells them apart.
    for library, expected in (((), portuguese), (("en",), _SYNOPSIS)):
        field, proposal = _preview(
            _regional(report, {}, library=library), "description_fill"
        )
        assert (field.outcome, field.desired_value) == ("projected", expected)
        assert len(_result(proposal, TargetDestination.DESCRIPTION).alternatives) == 2


def test_a_lone_foreign_synopsis_asks_and_says_why():
    report = _regional(
        recording_synopsis_report(None, (_SYNOPSIS,)),
        {"B000000001": "de"},
        local="en",
    )
    field, proposal = _preview(report, "description_fill")
    assert (field.outcome, field.reason) == ("abstain", "candidate_language_differs")
    assert len(_result(proposal, TargetDestination.DESCRIPTION).alternatives) == 1
