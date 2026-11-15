"""Refused WORK answers kept for ADR-0096's level path (#1355, S11, S6 rule 3).

A WORK answer that set admission refuses qualifies to `NO_MATCH` for every
live consumer, and its products are kept whole beside the observation:
the union over the rungs and lanes that refused, at most five, with every
drop by that bound or the report's byte budget counted. Only the level seam
reads them, and an observation that lost any admits none of them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderLaneResult,
    TargetSnapshot,
    metadata_discovery_plan,
    observe_work_ladder,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_IDENTITY_REFUSED_BYTES,
    MAX_REPORT_BYTES,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    default_target_fields,
    finalize_candidate_comparisons,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import (
    LevelAdmissionRecord,
    evaluate_report,
    provider_candidate_level_admission,
)
from mechanical_librarian.domain.work_identity import WorkDispatchLineage, WorkQuery
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.support.metadata_report_doubles import single_track_audio_facts

TITLE = "Example Book: A Test"
AUTHOR = "R. F. Example"
# The same person with the initials respelled: the closed comparison refuses
# it, and the level path's fold admits it.
FOLDED = "R.F. Example"
PROVIDER = "google_books"
_EXACT = WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
_WIDE = WorkIdentityLevel.TITLE_AND_CREATOR


def _fields() -> tuple[TargetField, ...]:
    return tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (AUTHOR,))
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(TITLE)
    )


def _plan() -> MetadataDiscoveryPlan:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=TITLE,
        canonical=TITLE,
        excerpt=TITLE,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return metadata_discovery_plan(
        TargetSnapshot("ebook", TITLE, (), fields=_fields()), (claim,)
    )


def _work_lane() -> MetadataDiscoveryLane:
    (lane,) = (value for value in _plan().lanes if value.kind is ProviderQueryKind.WORK)
    return lane


def _lookup(*, audio: bool = False) -> MetadataLookup:
    lane = _work_lane()
    return MetadataLookup(
        ProviderQueryKind.WORK,
        _plan().local_claims,
        lane.title,
        lane.authors,
        audio=single_track_audio_facts() if audio else None,
    )


def _candidate(
    identity: str,
    *,
    title: str = TITLE,
    author: str = FOLDED,
    work: str = "unresolved",
    descriptions: int = 0,
    provider: str = PROVIDER,
) -> ProviderCandidate:
    specifications = (
        (ClaimKind.TITLE, title, None),
        (ClaimKind.CREATOR, author, "author"),
        *((ClaimKind.DESCRIPTION, "\U0001f4da" * 16_384, None),) * descriptions,
    )
    return ProviderCandidate(
        identity,
        work,
        tuple(
            ProviderClaim.create(
                provider=provider,
                candidate=identity,
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=role,
                raw_order=order,
            )
            for order, (kind, value, role) in enumerate(specifications)
        ),
    )


def _answer(
    *candidates: ProviderCandidate, status: ProviderStatus = ProviderStatus.CANDIDATES
) -> ProviderObservation:
    return ProviderObservation(
        PROVIDER,
        status,
        (),
        tuple(sorted(candidates, key=lambda value: value.identity)),
        ProviderQueryKind.WORK,
        PROVIDER,
    )


def _rung(
    *candidates: ProviderCandidate,
) -> Callable[[WorkQuery], ProviderObservation]:
    """One rung's answer, dispatched as that rung's query."""
    if not candidates:
        return lambda _query: _answer(status=ProviderStatus.NO_MATCH)
    return lambda query: replace(
        _answer(*candidates), dispatch_lineage=WorkDispatchLineage.from_query(query)
    )


def _refused_lane(*refused: ProviderCandidate) -> ProviderLaneResult:
    return ProviderLaneResult(
        _work_lane(), ProviderStatus.NO_MATCH, identity_refused_candidates=refused
    )


def _report(*providers: ProviderObservation) -> MetadataReport:
    plan = _plan()
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", _fields()),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=plan.local_claims,
        local_reasons=("no_opening_claims",),
        providers=providers,
    )
    return MetadataReport.from_json(report.to_json())


def _padding(descriptions: int) -> ProviderObservation:
    """A live identifier answer that brings the report near its byte limit."""
    return ProviderObservation(
        "open_library",
        ProviderStatus.CANDIDATES,
        (),
        (_candidate("padding-1", descriptions=descriptions, provider="open_library"),),
        ProviderQueryKind.ISBN,
        "open_library",
    )


def _finalized(candidate: ProviderCandidate) -> object:
    """A record's document as report construction finalizes it."""
    return finalize_candidate_comparisons(
        candidate, _plan().local_claims, PROVIDER
    ).to_data()


def _forged(report: MetadataReport, change: Callable[[dict[str, Any]], None]) -> str:
    """The report's document after `change` edits it, readdressed."""
    data = json.loads(report.to_json())
    change(data)
    data.pop("report_id")
    data["report_id"] = report_content_address(data)
    return canonical_json(data)


# --- Test 15: retention -----------------------------------------------------


def test_refused_work_answer_stays_no_match_for_live_consumers() -> None:
    page = (_candidate("volume-1"), _candidate("volume-2", work="work-2"))
    qualified = qualify_provider_observation(_answer(*page), _lookup())
    retained = _report(provider_observation(PROVIDER, (_refused_lane(*page),)))
    plain = _report(provider_observation(PROVIDER, (_refused_lane(),)))

    assert (ProviderStatus.NO_MATCH, (), page) == (
        qualified.status,
        qualified.candidates,
        qualified.identity_refused_candidates,
    )
    (provider,) = retained.providers
    assert (ProviderStatus.NO_MATCH, (), 0) == (
        provider.status,
        provider.candidates,
        provider.identity_refused_truncated,
    )
    assert ("volume-1", "volume-2") == tuple(
        value.identity for value in provider.identity_refused_candidates
    )
    # Every live reading is the reading of the same report without them.
    assert retained.identifier_evidence == plain.identifier_evidence
    assert [value.to_data() for value in evaluate_report(retained).results] == [
        value.to_data() for value in evaluate_report(plain).results
    ]


def test_an_ebook_ladder_keeps_its_refused_rung() -> None:
    page = (_candidate("volume-1"),)
    lane = observe_work_ladder(_lookup(), _rung(*page))
    assert (ProviderStatus.NO_MATCH, page) == (
        lane.observation.status,
        lane.identity_refused_candidates,
    )


@pytest.mark.parametrize(
    "status",
    (
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
        ProviderStatus.MALFORMED,
        ProviderStatus.UNAVAILABLE,
    ),
    ids=("overflow", "malformed", "error"),
)
def test_an_answer_that_is_not_a_refusal_keeps_nothing(status: ProviderStatus) -> None:
    qualified = qualify_provider_observation(_answer(status=status), _lookup())
    assert (status, ()) == (qualified.status, qualified.identity_refused_candidates)


def test_a_later_rung_that_admits_empties_the_retained_set() -> None:
    answers = iter(
        (
            _rung(_candidate("volume-1", author="Other Person")),
            _rung(_candidate("volume-2", author=AUTHOR)),
        )
    )
    lane = observe_work_ladder(
        _lookup(audio=True),
        lambda query: next(answers)(query),
    )
    assert (ProviderStatus.CANDIDATES, ()) == (
        lane.observation.status,
        lane.identity_refused_candidates,
    )


def test_another_lane_that_admits_empties_every_lane() -> None:
    admitted = ProviderLaneResult(
        replace(_work_lane(), identity="lane_" + "1" * 64),
        ProviderStatus.CANDIDATES,
        (_candidate("volume-9", author=AUTHOR),),
    )
    observation = provider_observation(
        PROVIDER, (_refused_lane(_candidate("volume-1")), admitted)
    )
    assert ((), 0) == (
        observation.identity_refused_candidates,
        observation.identity_refused_truncated,
    )
    assert all(not lane.identity_refused_new for lane in observation.lanes)


def test_identity_refused_union_capped_with_truncation() -> None:
    # Two refused rungs; the second repeats the first rung's last product.
    pages = iter(
        (
            _rung(*(_candidate(f"volume-{index}") for index in (1, 2, 3))),
            _rung(*(_candidate(f"volume-{index}") for index in (3, 4, 5, 6))),
            _rung(),
        )
    )
    lane = observe_work_ladder(
        _lookup(audio=True),
        lambda query: next(pages)(query),
    )
    observation = provider_observation(
        PROVIDER, (_refused_lane(*lane.identity_refused_candidates),)
    )

    assert (
        "volume-1",
        "volume-2",
        "volume-3",
        "volume-4",
        "volume-5",
        "volume-6",
    ) == tuple(value.identity for value in lane.identity_refused_candidates)
    assert (
        ("volume-1", "volume-2", "volume-3", "volume-4", "volume-5"),
        1,
    ) == (
        tuple(value.identity for value in observation.identity_refused_candidates),
        observation.identity_refused_truncated,
    )
    (recorded,) = observation.lanes
    assert tuple(f"volume-{index}" for index in range(1, 7)) == (
        recorded.identity_refused_new
    )
    (stored,) = _report(observation).providers
    assert (5, 1) == (
        len(stored.identity_refused_candidates),
        stored.identity_refused_truncated,
    )


# --- Test 16: the byte budget and a truncated set ---------------------------


def _bytes(candidate: ProviderCandidate) -> int:
    return len(canonical_json(candidate.to_data()).encode("utf-8"))


def _size(report: MetadataReport) -> int:
    return len(report.to_json().encode("utf-8"))


def test_report_at_byte_limit_drops_retained_refused_first() -> None:
    refused = tuple(_candidate(f"volume-{index}", descriptions=1) for index in range(5))
    # The most live padding that still leaves room for two retained records.
    room = MAX_REPORT_BYTES - 2 * (_bytes(refused[0]) + 1)
    padding = 0
    while _size(_report(_padding(padding + 1))) <= room:
        padding += 1
    report = _report(
        provider_observation(PROVIDER, (_refused_lane(*refused),)), _padding(padding)
    )
    provider, padded = report.providers
    kept = tuple(value.identity for value in provider.identity_refused_candidates)

    assert _size(report) <= MAX_REPORT_BYTES
    assert padded.candidates == _report(_padding(padding)).providers[0].candidates
    assert 2 <= len(kept) < 5
    assert tuple(f"volume-{index}" for index in range(len(kept))) == kept
    assert 5 - len(kept) == provider.identity_refused_truncated
    # Keeping one more record would have passed the limit.
    data = report.to_data()
    data["providers"] = [
        replace(
            provider,
            identity_refused_candidates=(
                *provider.identity_refused_candidates,
                refused[len(kept)],
            ),
            identity_refused_truncated=provider.identity_refused_truncated - 1,
        ).to_data(),
        padded.to_data(),
    ]
    assert len(canonical_json(data).encode("utf-8")) > MAX_REPORT_BYTES


def test_retained_refused_answers_stay_inside_their_byte_budget() -> None:
    refused = tuple(_candidate(f"volume-{index}", descriptions=2) for index in range(5))
    report = _report(provider_observation(PROVIDER, (_refused_lane(*refused),)))
    (provider,) = report.providers
    sizes = [_bytes(value) for value in provider.identity_refused_candidates]
    kept = len(sizes)

    assert sum(sizes) + kept - 1 <= MAX_IDENTITY_REFUSED_BYTES
    assert sum(sizes) + kept + sizes[0] > MAX_IDENTITY_REFUSED_BYTES
    assert (5 - kept, tuple(f"volume-{index}" for index in range(kept))) == (
        provider.identity_refused_truncated,
        tuple(value.identity for value in provider.identity_refused_candidates),
    )

    def restore(data: dict[str, Any]) -> None:
        provider = _stored(data)
        provider["identity_refused_candidates"].append(
            _finalized(_candidate(f"volume-{kept}", descriptions=2))
        )
        provider["identity_refused_truncated"] = 4 - kept

    with pytest.raises(ReportValueError, match="exceed their byte budget"):
        MetadataReport.from_json(_forged(report, restore))


@pytest.mark.parametrize("bound", ("count_cap", "byte_budget"))
def test_truncated_refused_set_admits_nothing(bound: str) -> None:
    """Two works met the level before truncation and one after (S6 rule 3)."""
    first = _candidate("volume-a", work="work-1")
    second = _candidate(
        "volume-z", work="work-2", descriptions=9 if bound == "byte_budget" else 0
    )
    others = tuple(
        _candidate(f"volume-{index}", title="Another Book")
        for index in ("b", "c", "d")
        if bound == "count_cap"
    )
    # The count cap keeps the first five in rung order; the byte budget drops
    # the last identity first.
    order = (*others, _candidate("volume-e", title="Another Book"), first, second)
    report = _report(provider_observation(PROVIDER, (_refused_lane(*order),)))
    (provider,) = report.providers
    retained = {value.identity: value for value in provider.identity_refused_candidates}

    assert "volume-z" not in retained
    assert provider.identity_refused_truncated == 1
    for level in (_EXACT, _WIDE):
        for candidate in retained.values():
            assert provider_candidate_level_admission(
                report, provider, candidate, level
            ) == LevelAdmissionRecord(None, "truncated")


def test_an_untruncated_refused_set_reaches_the_level_seam() -> None:
    """The control: the same lone work, nothing dropped, is admitted by the fold."""
    report = _report(
        provider_observation(PROVIDER, (_refused_lane(_candidate("volume-a")),))
    )
    (provider,) = report.providers
    (candidate,) = provider.identity_refused_candidates
    verdict = provider_candidate_level_admission(report, provider, candidate, _EXACT)
    assert verdict is not None and verdict.admission is not None
    assert (_EXACT, "initials_fold") == (
        verdict.admission.level,
        verdict.creator_comparison,
    )


# --- Test 17: readback ------------------------------------------------------


def _refused_report() -> MetadataReport:
    return _report(
        provider_observation(
            PROVIDER,
            (_refused_lane(_candidate("volume-1"), _candidate("volume-2")),),
        )
    )


def _stored(data: dict[str, Any]) -> dict[str, Any]:
    (provider,) = data["providers"]
    return provider


def _add_admissible(data: dict[str, Any]) -> None:
    """Retain only a record that set admission would have admitted."""
    provider = _stored(data)
    provider["identity_refused_candidates"] = [
        _finalized(_candidate("volume-0", author=AUTHOR))
    ]
    provider["lanes"][0]["identity_refused_new"] = ["volume-0"]


def _drop_lane_identity(data: dict[str, Any]) -> None:
    _stored(data)["lanes"][0]["identity_refused_new"] = ["volume-1"]


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (_add_admissible, "refused work answers are admitted"),
        (_drop_lane_identity, "refused work answers do not match their lanes"),
    ),
    ids=("admitted_by_set_rule", "missing_from_lanes"),
)
def test_forged_identity_refused_candidate_refused(
    change: Callable[[dict[str, Any]], None], message: str
) -> None:
    report = _refused_report()
    assert MetadataReport.from_json(report.to_json()) == report
    with pytest.raises(ReportValueError, match=message):
        MetadataReport.from_json(_forged(report, change))


def test_an_admissible_refused_set_is_kept_only_as_truncated() -> None:
    """A set admission admits is no refusal, so no report keeps it as one."""
    report = _report(
        provider_observation(
            PROVIDER, (_refused_lane(_candidate("volume-1", author=AUTHOR)),)
        )
    )
    (provider,) = report.providers
    assert ((), 1, ("volume-1",)) == (
        provider.identity_refused_candidates,
        provider.identity_refused_truncated,
        provider.lanes[0].identity_refused_new,
    )


def _observation(
    lanes: tuple[tuple[str, ...], ...], retained: tuple[str, ...], truncated: int
) -> ProviderObservation:
    return ProviderObservation(
        PROVIDER,
        ProviderStatus.NO_MATCH,
        ("local_" + "0" * 64,),
        (),
        ProviderQueryKind.WORK if len(lanes) == 1 else ProviderQueryKind.DISCOVERY,
        PROVIDER,
        tuple(
            ProviderLaneObservation(
                "lane_" + str(index) * 64,
                ProviderQueryKind.WORK,
                ProviderStatus.NO_MATCH,
                ("local_" + "0" * 64,),
                identity_refused_new=identities,
            )
            for index, identities in enumerate(lanes, 1)
        ),
        identity_refused_candidates=tuple(_candidate(value) for value in retained),
        identity_refused_truncated=truncated,
    )


@pytest.mark.parametrize(
    ("lanes", "retained", "truncated"),
    (
        ((("volume-1",),), ("volume-1",), 1),
        ((("volume-1",),), ("volume-1", "volume-2"), 0),
        ((("volume-1", "volume-2"),), ("volume-1",), 0),
        ((("volume-1",), ("volume-1",)), ("volume-1",), 1),
    ),
    ids=(
        "repeated_across_rungs_counted_twice",
        "retained_identity_missing_from_lanes",
        "lane_identity_neither_retained_nor_truncated",
        "repeated_across_lanes",
    ),
)
def test_identity_refused_count_must_match_rung_diagnostics(
    lanes: tuple[tuple[str, ...], ...], retained: tuple[str, ...], truncated: int
) -> None:
    assert _observation((("volume-1",),), ("volume-1",), 0).lanes[
        0
    ].identity_refused_new == ("volume-1",)
    with pytest.raises(ReportValueError, match="do not match their lanes"):
        _observation(lanes, retained, truncated)
