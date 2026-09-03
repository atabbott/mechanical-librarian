"""Cross-source corroboration of subtitles by stripped title segments."""

from __future__ import annotations

from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalResult,
    ProposalScope,
    evaluate_report,
    segment_corroboration,
    subtitle_segment_evidence,
)
from tests.support.proposal_evidence import (
    _fields,
    _report,
)
from tests.support.proposal_evidence import (
    consensus_local_title as _local_title,
)
from tests.support.proposal_evidence import (
    consensus_provider_title as _provider_title,
)

_SEGMENTED = "Plain Work: A Second Look"
_SEGMENT = "A Second Look"


def _subtitle_result(
    *, current: str, local: str, providers: tuple
) -> tuple[ProposalResult, MetadataReport]:
    report = _report(
        title=current,
        fields=_fields(
            current,
            title=TargetField(
                TargetDestination.TITLE,
                TargetFieldState.VALUE,
                current,
            ),
            subtitle=TargetField(
                TargetDestination.SUBTITLE,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=(_local_title(local),),
        providers=providers,
    )
    results = {result.destination: result for result in evaluate_report(report).results}
    return results[TargetDestination.SUBTITLE], report


def _stated_correlations(result: ProposalResult, value: str) -> tuple[str, ...]:
    """The correlations of the candidate stating one subtitle value."""
    return next(
        alternative.correlations
        for alternative in result.alternatives
        if alternative.value.value == value
    )


def test_a_provider_subtitle_dissent_withholds_segment_corroboration() -> None:
    result, _ = _subtitle_result(
        current="Plain Work",
        local="Plain Work",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle=_SEGMENT),
            _provider_title("hardcover", _SEGMENTED),
            _provider_title("inventaire", "Plain Work", subtitle="Other Look"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert (
        "competing_evidence",
        "subtitle_segment_provider_dissent",
    ) == result.reasons


def test_more_decorated_authorities_than_plain_withhold_corroboration() -> None:
    result, report = _subtitle_result(
        current="Plain Work",
        local="Plain Work",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle=_SEGMENT),
            _provider_title("hardcover", _SEGMENTED),
            _provider_title("inventaire", _SEGMENTED),
        ),
    )

    corroboration = segment_corroboration(
        report, _SEGMENT, _stated_correlations(result, _SEGMENT)
    )

    assert "subtitle_segment_decorated_consensus" == corroboration.withheld_reason
    assert () == corroboration.segments


def test_a_local_plain_form_counts_against_one_decoration() -> None:
    result, report = _subtitle_result(
        current="Plain Work",
        local="Plain Work",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle=_SEGMENT),
            _provider_title("hardcover", _SEGMENTED),
        ),
    )

    corroboration = segment_corroboration(
        report, _SEGMENT, _stated_correlations(result, _SEGMENT)
    )

    assert corroboration.withheld_reason is None
    assert (_SEGMENT,) == tuple(segment.value for segment in corroboration.segments)


def test_a_decoration_only_the_book_itself_carries_is_withheld() -> None:
    result, report = _subtitle_result(
        current=_SEGMENTED,
        local=_SEGMENTED,
        providers=(_provider_title("open_library", "Plain Work", subtitle=_SEGMENT),),
    )

    corroboration = segment_corroboration(
        report, _SEGMENT, _stated_correlations(result, _SEGMENT)
    )

    assert "subtitle_segment_decorated_consensus" == corroboration.withheld_reason


def test_a_segment_never_corroborates_its_own_source() -> None:
    result, _ = _subtitle_result(
        current="Plain Work",
        local="Plain Work",
        providers=(
            _provider_title("open_library", _SEGMENTED, subtitle=_SEGMENT),
            _provider_title("hardcover", "Plain Work"),
            _provider_title("inventaire", "Plain Work", subtitle="Other Look"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert (
        "competing_evidence",
        "subtitle_segment_uncorroborated",
    ) == result.reasons


def test_a_segment_never_elects_a_subtitle_on_its_own() -> None:
    result, _ = _subtitle_result(
        current=_SEGMENTED,
        local=_SEGMENTED,
        providers=(_provider_title("open_library", "Plain Work"),),
    )

    assert ProposalOutcome.INELIGIBLE is result.outcome
    assert (
        "no_supported_evidence",
        "subtitle_segment_uncorroborated",
    ) == result.reasons


def test_a_generic_segment_stays_ineligible() -> None:
    result, _ = _subtitle_result(
        current="Plain Work: A Novel",
        local="Plain Work: A Novel",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle="A Novel"),
            _provider_title("hardcover", "Plain Work", subtitle="A Novel"),
        ),
    )

    assert ProposalOutcome.INELIGIBLE is result.outcome
    assert ("generic_provider_subtitle",) == result.reasons


def test_a_generic_collection_label_is_rejected_before_corroboration() -> None:
    result, _ = _subtitle_result(
        current="Plain Work",
        local="Plain Work",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle="Stories"),
            _provider_title("hardcover", "Plain Work: Stories"),
        ),
    )

    assert ProposalOutcome.INELIGIBLE is result.outcome
    assert ("generic_provider_subtitle",) == result.reasons


def test_segment_evidence_inherits_work_scope_and_its_source_correlation() -> None:
    _, report = _subtitle_result(
        current="Plain Work",
        local="Plain Work",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle=_SEGMENT),
            _provider_title("hardcover", _SEGMENTED),
        ),
    )

    segments = subtitle_segment_evidence(report)

    assert 1 == len(segments)
    assert _SEGMENT == segments[0].value
    assert "Plain Work" == segments[0].clean_title
    assert ProposalScope.WORK is segments[0].scope
    assert () == subtitle_segment_evidence(report, "Another Look")
