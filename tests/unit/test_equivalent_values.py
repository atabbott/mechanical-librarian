"""Equivalent titles and language spellings that never reach the owner (#1496)."""

from __future__ import annotations

from contextlib import closing

import pytest

from mechanical_librarian.domain.field_value_comparison import (
    title_article_key,
    title_has_leading_article,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import (
    EVIDENCE_LED_TITLE_REASON,
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    evaluate_report,
)
from mechanical_librarian.domain.work_identity import work_title_election_matches
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.language_reports import language_report
from tests.support.proposal_evidence import consensus_provider_title
from tests.unit.test_decoration_equivalence_titles import _title_result


def _result(report: MetadataReport, destination: TargetDestination) -> ProposalResult:
    return next(
        result
        for result in evaluate_report(report).results
        if result.destination is destination
    )


def _isbn_title(current: str, titles: tuple[str, ...]) -> ProposalResult:
    report = MetadataReport.from_json(
        isbn_title_report(current=current, titles=titles).to_json()
    )
    return _result(report, TargetDestination.TITLE)


@pytest.mark.parametrize(
    ("value", "key", "article"),
    (
        ("The Example Title", "example title", True),
        ("A Example Title", "example title", True),
        ("An  Example Title", "example title", True),
        ("the example title", "example title", True),
        ("Example Title", "example title", False),
        ("The", "the", False),
        ("Theory of Everything", "theory of everything", False),
        ("Anathem", "anathem", False),
        ("Example The Title", "example the title", False),
    ),
    ids=(
        "the",
        "a",
        "an_spaced",
        "lower_the",
        "bare",
        "article_alone",
        "the_prefix_word",
        "an_prefix_word",
        "inner_article",
    ),
)
def test_title_article_key_folds_one_leading_article_only(value, key, article):
    assert title_article_key(value) == key
    assert title_has_leading_article(value) is article


def test_a_provider_dropping_the_article_keeps_the_title_with_it():
    result = _isbn_title(
        "The Example Title",
        ("The Example Title", "Example Title", "The Example Title"),
    )

    assert (ProposalOutcome.KEEP, ("positive_preservation_evidence",)) == (
        result.outcome,
        result.reasons,
    )
    (kept,) = result.alternatives
    assert ("The Example Title", ProposalAssessment.PRESERVES_TARGET) == (
        kept.value.value,
        kept.assessment,
    )


def test_an_isbn_election_elects_the_form_with_the_article():
    result = _isbn_title(
        "Example Series 03 - The Example Title", ("The Example Title", "Example Title")
    )

    assert (ProposalOutcome.PROPOSE_CHANGE, (EVIDENCE_LED_TITLE_REASON,)) == (
        result.outcome,
        result.reasons,
    )
    selected = next(
        value
        for value in result.alternatives
        if value.alternative_id == result.selected_alternative_id
    )
    assert "The Example Title" == selected.value.value
    assert "Example Title" not in {value.value.value for value in result.alternatives}


@pytest.mark.parametrize(
    "other",
    (
        "Example Titles",
        "Example Title Two",
        "The Other Title",
        "Example",
        "A Example Title",
    ),
    ids=("plural", "extra_word", "other_title", "missing_word", "other_article"),
)
def test_a_title_differing_in_anything_else_still_conflicts(other):
    result = _isbn_title("The Example Title", ("The Example Title", other))

    assert (ProposalOutcome.UNRESOLVED_CHOICE, ("isbn_title_material_conflict",)) == (
        result.outcome,
        result.reasons,
    )
    assert other in {value.value.value for value in result.alternatives}


def test_the_bare_form_is_never_offered_as_a_replacement_for_the_article():
    # Only the current title carries the article: the bare provider form names
    # the same title and preserves it.
    result = _title_result(
        current="The Plain Work",
        local="Plain Work",
        providers=(
            consensus_provider_title("open_library", "Plain Work"),
            consensus_provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.KEEP is result.outcome
    (kept,) = result.alternatives
    assert ("The Plain Work", ProposalAssessment.PRESERVES_TARGET) == (
        kept.value.value,
        kept.assessment,
    )


def test_a_bare_form_beside_two_articles_joins_neither():
    result = _isbn_title(
        "Example Series 03 - The Example Title",
        ("The Example Title", "Example Title", "A Example Title"),
    )

    assert (ProposalOutcome.UNRESOLVED_CHOICE, ("isbn_title_material_conflict",)) == (
        result.outcome,
        result.reasons,
    )
    assert {"The Example Title", "Example Title", "A Example Title"} <= {
        value.value.value for value in result.alternatives
    }


def test_another_article_never_replaces_the_current_one():
    result = _title_result(
        current="The Plain Work",
        providers=(
            consensus_provider_title("open_library", "A Plain Work"),
            consensus_provider_title("hardcover", "A Plain Work"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is not result.outcome
    assert "The Plain Work" in {value.value.value for value in result.alternatives}


@pytest.mark.parametrize(
    "providers",
    (("Plain’s Work", "Plain's Work"), ("Plain's Work", "Plain's Work")),
    ids=("one_each", "both_straight"),
)
def test_a_curly_apostrophe_is_still_its_own_title(providers):
    result = _title_result(
        current="Plain’s Work",
        providers=tuple(
            consensus_provider_title(name, value)
            for name, value in zip(
                ("open_library", "hardcover"), providers, strict=True
            )
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is not result.outcome
    assert "Plain’s Work" in {value.value.value for value in result.alternatives}


def test_a_current_title_without_the_article_is_given_it():
    result = _title_result(
        current="Plain Work",
        local="The Plain Work",
        providers=(
            consensus_provider_title("open_library", "The Plain Work"),
            consensus_provider_title("hardcover", "Plain Work"),
        ),
    )

    (alternative,) = result.alternatives
    assert ("The Plain Work", ProposalAssessment.REPLACES_TARGET) == (
        alternative.value.value,
        alternative.assessment,
    )


@pytest.mark.parametrize(
    "decorated",
    (
        "Plain Work [ISBN 9780306406157]",
        "Plain Work (ISBN 9780306406157)",
        "Plain Work [ISBN: 978-0-306-40615-7]",
        "Plain Work (isbn 0306406152)",
    ),
    ids=("square", "round", "square_hyphenated", "round_isbn10"),
)
def test_a_bracketed_isbn_suffix_is_an_explicit_identifier(decorated):
    (match,) = work_title_election_matches(decorated, ())
    assert ("explicit_identifier", "Plain Work") == (
        match.family.value,
        match.clean_title,
    )


@pytest.mark.parametrize(
    "decorated",
    (
        "Plain Work [ISBN 9780306406158]",
        "Plain Work [ASIN B000000000]",
        "Plain Work [ISBN 9780306406157] Extra",
        "Plain Work [see ISBN 9780306406157]",
    ),
    ids=("bad_checksum", "asin", "trailing_text", "other_words"),
)
def test_a_bracket_that_is_not_one_valid_isbn_is_no_identifier(decorated):
    assert all(
        match.family.value != "explicit_identifier"
        for match in work_title_election_matches(decorated, ())
    )


@pytest.mark.parametrize(
    "decorated",
    ("Plain Work [ISBN 9780306406157]", "Plain Work (ISBN 9780306406157)"),
    ids=("square", "round"),
)
def test_a_title_route_bracketed_isbn_is_tidied_by_consensus(decorated):
    result = _title_result(
        current=decorated,
        providers=(
            consensus_provider_title("open_library", "Plain Work"),
            consensus_provider_title("hardcover", "Plain Work"),
        ),
    )

    assert (
        ProposalOutcome.PROPOSE_CHANGE,
        ("provider_consensus_title_explicit_identifier",),
    ) == (result.outcome, result.reasons)
    assert ["Plain Work"] == [value.value.value for value in result.alternatives]


def test_a_title_route_bracketed_isbn_is_tidied_by_decoration_equivalence():
    decorated = "Plain Work [ISBN 9780306406157]"
    result = _title_result(
        current=decorated,
        providers=(
            consensus_provider_title("open_library", "Plain Work"),
            consensus_provider_title("hardcover", decorated),
        ),
    )

    assert (
        ProposalOutcome.PROPOSE_CHANGE,
        ("decoration_tidy_explicit_identifier",),
    ) == (result.outcome, result.reasons)


def test_one_provider_alone_never_tidies_a_bracketed_isbn():
    result = _title_result(
        current="Plain Work [ISBN 9780306406157]",
        providers=(consensus_provider_title("open_library", "Plain Work"),),
    )

    assert (ProposalOutcome.UNRESOLVED_CHOICE, ("competing_evidence",)) == (
        result.outcome,
        result.reasons,
    )


def test_a_respelling_beside_another_language_is_no_choice_of_its_own():
    result = _result(
        language_report(("English", "Dutch"), "nl"), TargetDestination.LANGUAGE
    )

    assert (ProposalOutcome.UNRESOLVED_CHOICE, ("field_policy_conflict",)) == (
        result.outcome,
        result.reasons,
    )
    assert ["English"] == [value.value.value for value in result.alternatives]


def test_a_respelling_beside_another_language_leaves_only_the_other():
    result = _result(
        language_report(("English", "French"), "en"), TargetDestination.LANGUAGE
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert ["French"] == [value.value.value for value in result.alternatives]


def test_two_other_languages_both_stay_beside_the_current_one():
    result = _result(
        language_report(("English", "French"), "nl"), TargetDestination.LANGUAGE
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert {"English", "French"} == {value.value.value for value in result.alternatives}


@pytest.mark.parametrize("audio", (False, True))
@pytest.mark.parametrize(
    ("current", "values", "automatic"),
    (
        ("en", ("English",), True),
        ("en-GB", ("en",), True),
        ("english", ("English", "eng"), True),
        ("nl", ("English",), False),
        ("zh-Hans", ("zh-Hant",), False),
    ),
    ids=("code", "regional", "lower_name", "other_base", "other_script"),
)
def test_a_respelling_is_written_automatically_and_another_language_is_not(
    tmp_path, audio, current, values, automatic
):
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from tests.support.audiobook_reports import publish_audiobook_preview

    report = language_report(values, current, audio=audio)
    with closing(open_repository(tmp_path / "language.sqlite")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository, report, ("language_rendering",)
        )
        repository.persist_shadow_outcome(case_id, 4)
        field = next(
            value
            for value in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if value["destination"] == "language"
        )
    # Dropping a region on an audiobook is a replace F46 never measured.
    if automatic and not (audio and "-" in current):
        assert field["automatic_decision"]["selected_value"] == {
            "kind": "text",
            "value": "English",
        }
    else:
        assert field["automatic_decision"] is None


@pytest.mark.parametrize(
    "decorated",
    ("Plain Work [ISBN 9780306406157]", "Plain Work (ISBN 9780306406157)"),
    ids=("square", "round"),
)
def test_a_bracketed_isbn_leaves_no_empty_bracket_in_the_lookup(decorated):
    from mechanical_librarian.domain.work_identity import work_query_ladder

    assert ["Plain Work"] == [
        query.title for query in work_query_ladder(decorated, ("Ada Author",))
    ]


def test_stacked_bracketed_isbns_stay_competing():
    result = _title_result(
        current="Plain Work [ISBN 9780306406157] (ISBN 9780306406157)",
        providers=(
            consensus_provider_title("open_library", "Plain Work"),
            consensus_provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is not result.outcome


def test_an_article_kept_for_the_current_title_keeps_a_different_apostrophe():
    result = _title_result(
        current="The Plain’s Work",
        local="The Plain’s Work",
        providers=(
            consensus_provider_title("open_library", "Plain's Work"),
            consensus_provider_title("hardcover", "Plain's Work"),
        ),
    )

    offered = [value.value.value for value in result.alternatives]
    assert len(offered) == len(set(offered))
    assert "Plain's Work" in offered
