"""Cross-source corroboration of subtitles by stripped title segments."""

from __future__ import annotations

from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalResult,
    ProposalScope,
    evaluate_report,
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
) -> tuple[ProposalResult, object]:
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


def test_a_provider_title_segment_corroborates_another_provider_subtitle() -> None:
    result, report = _subtitle_result(
        current="Plain Work",
        local="Plain Work",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle=_SEGMENT),
            _provider_title("hardcover", _SEGMENTED),
            _provider_title("inventaire", "Plain Work", subtitle="Other Look"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert ("provider_segment_corroborated_subtitle",) == result.reasons
    assert _SEGMENT == result.alternatives[0].value.value
    assert ProposalScope.WORK is result.alternatives[0].scope
    assert {
        segment.reference for segment in subtitle_segment_evidence(report, _SEGMENT)
    } & set(result.alternatives[0].evidence_references)


def test_a_local_title_segment_still_reports_local_attestation() -> None:
    result, _ = _subtitle_result(
        current=_SEGMENTED,
        local=_SEGMENTED,
        providers=(
            _provider_title("open_library", "Plain Work", subtitle=_SEGMENT),
            _provider_title("hardcover", "Plain Work", subtitle="Other Look"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert ("local_attested_subtitle",) == result.reasons


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
    assert ProposalScope.WORK is segments[0].scope
    assert () == subtitle_segment_evidence(report, "Another Look")
