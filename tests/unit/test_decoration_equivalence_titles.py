"""Closed decoration-equivalence title election cases."""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
    _report,
)
from tests.support.proposal_evidence import (
    consensus_local_title as _local_title,
)
from tests.support.proposal_evidence import (
    consensus_provider_title as _provider_title,
)

_PLAIN = "Plain Work"
_DECORATIONS = (
    ("bracket_source_tag", "Plain Work [retail]"),
    ("explicit_identifier", "Plain Work ISBN 9780306406157"),
    ("parenthesized_bare_number", "Plain Work (2)"),
    ("parenthesized_series_name", "Plain Work (The Sequence)"),
    ("parenthesized_year", "Plain Work (2012)"),
    ("subtitle_segment", "Plain Work: A Second Look"),
)


def _title_result(
    *,
    current: str,
    providers: tuple,
    local: str | None = None,
    series: tuple[TargetSeries, ...] | None = None,
) -> ProposalResult:
    overrides = {
        "title": TargetField(
            TargetDestination.TITLE,
            TargetFieldState.VALUE,
            current,
        )
    }
    if series is not None:
        overrides["series"] = TargetField(
            TargetDestination.SERIES,
            TargetFieldState.VALUE,
            series,
        )
    report = _report(
        title=current,
        fields=_fields(current, **overrides),
        local_claims=(_local_title(current if local is None else local),),
        providers=providers,
    )
    results = {result.destination: result for result in evaluate_report(report).results}
    return results[TargetDestination.TITLE]


def _pair(decorated: str) -> tuple:
    return (
        _provider_title("open_library", _PLAIN),
        _provider_title("hardcover", decorated),
    )


@pytest.mark.parametrize(("family", "decorated"), _DECORATIONS)
def test_holding_the_plain_form_keeps_it_with_no_write(
    family: str, decorated: str
) -> None:
    result = _title_result(current=_PLAIN, providers=_pair(decorated))

    assert ProposalOutcome.KEEP is result.outcome
    assert (f"decoration_equivalent_{family}",) == result.reasons
    assert result.selected_alternative_id is None
    assert 1 == len(result.alternatives)
    assert ProposalAssessment.PRESERVES_TARGET is result.alternatives[0].assessment
    assert _PLAIN == result.alternatives[0].value.value


@pytest.mark.parametrize(("family", "decorated"), _DECORATIONS)
def test_holding_the_decorated_form_tidies_it_to_the_plain_form(
    family: str, decorated: str
) -> None:
    result = _title_result(
        current=decorated,
        providers=_pair(decorated),
        series=(
            (TargetSeries("The Sequence", None),)
            if family == "parenthesized_series_name"
            else None
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert (f"decoration_tidy_{family}",) == result.reasons
    assert 1 == len(result.alternatives)
    assert result.selected_alternative_id == result.alternatives[0].alternative_id
    assert _PLAIN == result.alternatives[0].value.value


def test_parenthesized_series_tidy_requires_preserved_series_evidence() -> None:
    decorated = "Plain Work (The Sequence)"

    unpreserved = _title_result(current=decorated, providers=_pair(decorated))
    preserved = _title_result(
        current=decorated,
        providers=_pair(decorated),
        series=(TargetSeries("The Sequence", None),),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is unpreserved.outcome
    assert (
        "competing_evidence",
        "decoration_tidy_series_unpreserved",
    ) == unpreserved.reasons
    assert ProposalOutcome.PROPOSE_CHANGE is preserved.outcome


def test_keeping_the_plain_series_form_needs_no_series_evidence() -> None:
    result = _title_result(current=_PLAIN, providers=_pair("Plain Work (The Sequence)"))

    assert ProposalOutcome.KEEP is result.outcome
    assert ("decoration_equivalent_parenthesized_series_name",) == result.reasons


def test_stacked_decorations_stay_competing() -> None:
    decorated = "Plain Work: A Second Look (2012)"

    result = _title_result(current=decorated, providers=_pair(decorated))

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert ("competing_evidence",) == result.reasons


def test_substantively_different_titles_stay_competing() -> None:
    result = _title_result(
        current=_PLAIN,
        providers=(
            _provider_title("open_library", _PLAIN),
            _provider_title("hardcover", "Other Work"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert ("competing_evidence",) == result.reasons


def test_three_alternatives_never_form_a_class() -> None:
    result = _title_result(
        current="Plain Work [retail]",
        providers=(
            _provider_title("open_library", _PLAIN),
            _provider_title("hardcover", "Plain Work [retail]"),
            _provider_title("inventaire", "Other Work"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert ("competing_evidence",) == result.reasons


def test_a_locally_attested_form_never_pairs() -> None:
    result = _title_result(
        current="Plain Work [retail]",
        providers=(
            _provider_title("open_library", _PLAIN),
            _provider_title("hardcover", _PLAIN),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert ("provider_consensus_title_bracket_source_tag",) == result.reasons


def test_a_plain_local_only_form_stays_competing() -> None:
    result = _title_result(
        current=_PLAIN,
        providers=(
            _provider_title("open_library", "Plain Work [retail]"),
            _provider_title("hardcover", "Other Work"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert ("competing_evidence",) == result.reasons


def test_a_dissenting_provider_work_title_blocks_the_collapse() -> None:
    ambiguous = _provider(
        "inventaire",
        "inventaire-edition",
        (
            _claim(
                "inventaire",
                "inventaire-edition",
                ClaimKind.TITLE,
                "Other Work",
                scope=ClaimScope.WORK,
            ),
            _claim(
                "inventaire",
                "inventaire-edition",
                ClaimKind.TITLE,
                "Another Work",
                scope=ClaimScope.WORK,
                order=1,
            ),
        ),
        authority="inventaire",
    )

    result = _title_result(
        current="Plain Work [retail]",
        providers=(*_pair("Plain Work [retail]"), ambiguous),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert (
        "competing_evidence",
        "decoration_equivalence_provider_dissent",
    ) == result.reasons


def test_a_wider_decorated_attestation_blocks_the_collapse() -> None:
    result = _title_result(
        current=_PLAIN,
        providers=(
            _provider_title("open_library", "Plain Work [retail]"),
            _provider_title("hardcover", "Plain Work [retail]"),
            _provider_title("inventaire", _PLAIN),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert (
        "competing_evidence",
        "decoration_equivalence_decorated_consensus",
    ) == result.reasons


def test_attestation_width_counts_claims_a_rejected_candidate_still_states() -> None:
    result = _title_result(
        current="Plain Work [retail]",
        providers=(
            _provider_title("open_library", _PLAIN),
            _provider_title("hardcover", "Plain Work [retail]"),
            # A candidate WORK admission rejects still attests its title: the
            # differing manifestation title keeps it out of the alternatives.
            _provider_title(
                "inventaire",
                "Plain Work [retail]",
                edition_title="Different Edition Title",
            ),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert (
        "competing_evidence",
        "decoration_equivalence_decorated_consensus",
    ) == result.reasons
    assert 1 == len(
        [
            alternative
            for alternative in result.alternatives
            if alternative.value.value == "Plain Work [retail]"
        ]
    )


def test_case_only_provider_variants_count_as_one_attestation() -> None:
    result = _title_result(
        current="Plain Work [retail]",
        providers=(
            _provider_title("open_library", _PLAIN),
            _provider_title("inventaire", "plain work"),
            _provider_title("hardcover", "Plain Work [retail]"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert ("decoration_tidy_bracket_source_tag",) == result.reasons


def test_the_collapse_never_double_claims_a_consensus_election() -> None:
    consensus = _title_result(
        current="Plain Work [retail]",
        providers=(
            _provider_title("open_library", _PLAIN),
            _provider_title("hardcover", _PLAIN),
        ),
    )
    equivalence = _title_result(current=_PLAIN, providers=_pair("Plain Work [retail]"))

    assert consensus.reasons[0].startswith("provider_consensus_title_")
    assert not equivalence.reasons[0].startswith("provider_consensus_title_")
