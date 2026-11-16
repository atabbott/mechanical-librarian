"""What a report carries about its owner link, and how readback replays it (#1477)."""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    OwnerLinkFacts,
    ProviderCandidate,
    ProviderClaim,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    discovery_lane_identity,
    owner_link_admission,
    owner_link_anchor_values,
    provider_lane_candidate_reference,
    report_work_identity_context,
    work_discovery_lane_payloads,
)
from tests.support.metadata_report_schemas import default_target_fields

TITLE = "Local Title"
AUTHOR = "Local Author"
LINKED_TITLE = "Linked Title"
LINKED_AUTHOR = "Linked Author"
LINK_IDENTITY = "4321"
LINK_RECORD = "hardcover-work-0000004321"


def _fields() -> tuple[TargetField, ...]:
    return tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (AUTHOR,))
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(TITLE)
    )


def _title_claim() -> LocalClaim:
    return LocalClaim.create(
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


def _candidate(provider: str, identity: str, work: str) -> ProviderCandidate:
    return ProviderCandidate(
        identity,
        work,
        (
            ProviderClaim.create(
                provider=provider,
                candidate=identity,
                kind=ClaimKind.TITLE,
                value=LINKED_TITLE,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            ProviderClaim.create(
                provider=provider,
                candidate=identity,
                kind=ClaimKind.CREATOR,
                value=LINKED_AUTHOR,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=1,
            ),
        ),
    )


def _link_lane(
    provider: str, candidate: ProviderCandidate | None, *, status: ProviderStatus
) -> ProviderLaneObservation:
    identity = discovery_lane_identity({"kind": "link", "link": LINK_IDENTITY})
    return ProviderLaneObservation(
        identity,
        ProviderQueryKind.LINK,
        status,
        candidates=()
        if candidate is None
        else (
            ProviderLaneCandidate(
                provider_lane_candidate_reference(
                    provider, identity, candidate.identity, candidate.work_identity
                ),
                candidate.identity,
                candidate.work_identity,
            ),
        ),
    )


def _report(
    facts: OwnerLinkFacts | None,
    *,
    lane_status: ProviderStatus | None = ProviderStatus.CANDIDATES,
) -> MetadataReport:
    candidate = _candidate("hardcover", "hardcover-edition-1", LINK_RECORD)
    providers: tuple[ProviderObservation, ...] = ()
    if lane_status is not None:
        kept = candidate if lane_status is ProviderStatus.CANDIDATES else None
        providers = (
            ProviderObservation(
                "hardcover",
                lane_status,
                (),
                () if kept is None else (kept,),
                ProviderQueryKind.LINK,
                authority="hardcover",
                lanes=(_link_lane("hardcover", kept, status=lane_status),),
            ),
        )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", _fields()),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(_title_claim(),),
        local_reasons=("target_metadata",),
        providers=providers,
        owner_link=facts,
    )


def _facts(outcome: object = "admitted", **changes: object) -> OwnerLinkFacts:
    values: dict[str, object] = {
        "medium": "ebook",
        "scope": "work",
        "provider": "hardcover",
        "identity": LINK_IDENTITY,
        "outcome": outcome,
        "reason": None,
        "anchor": (LINKED_TITLE, (LINKED_AUTHOR,)) if outcome == "admitted" else None,
        **changes,
    }
    return OwnerLinkFacts(**values)  # type: ignore[arg-type]


def test_admitted_link_and_anchor_round_trip() -> None:
    report = _report(_facts())
    assert MetadataReport.from_json(report.to_json()) == report
    assert report.to_data()["owner_link"] == {
        "anchor": {"creators": [LINKED_AUTHOR], "title": LINKED_TITLE},
        "digest": report.owner_link.digest,  # type: ignore[union-attr]
        "identity": LINK_IDENTITY,
        "medium": "ebook",
        "outcome": "admitted",
        "provider": "hardcover",
        "reason": None,
        "scope": "work",
    }


def test_records_compare_with_the_anchor_not_the_local_values() -> None:
    report = _report(_facts())
    assert report_work_identity_context(report) == (LINKED_TITLE, (LINKED_AUTHOR,))
    unlinked = _report(None, lane_status=None)
    assert report_work_identity_context(unlinked) == (TITLE, (AUTHOR,))


def test_link_lane_admission_is_derived_not_stored() -> None:
    report = _report(_facts())
    assert owner_link_admission(report, "hardcover", "hardcover-edition-1") == (
        "owner_link"
    )
    assert owner_link_admission(report, "hardcover", "other") is None


def test_anchor_values_are_the_records_complete_work() -> None:
    candidate = _candidate("hardcover", "hardcover-edition-1", LINK_RECORD)
    assert owner_link_anchor_values(candidate) == (LINKED_TITLE, (LINKED_AUTHOR,))


def test_anchored_plan_asks_one_lane_with_the_anchor_or_none() -> None:
    claims = (_title_claim(),)
    (lane,) = work_discovery_lane_payloads(
        _fields(), claims, anchored=True, anchor=(LINKED_TITLE, (LINKED_AUTHOR,))
    )
    assert (lane.title, lane.authors, lane.anchor) == (
        LINKED_TITLE,
        (LINKED_AUTHOR,),
        True,
    )
    assert lane.input_references == (claims[0].reference,)
    assert work_discovery_lane_payloads(_fields(), claims, anchored=True) == ()


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param("missing", id="missing"),
        pytest.param("merged", id="merged-without-identity-diagnostic"),
    ],
)
def test_not_found_outcomes_replay_against_the_lanes(outcome: str) -> None:
    if outcome == "missing":
        report = _report(_facts("missing"), lane_status=ProviderStatus.NO_MATCH)
        assert MetadataReport.from_json(report.to_json()) == report
    else:
        with pytest.raises(ReportValueError, match="outcome does not replay"):
            _report(_facts("merged"), lane_status=ProviderStatus.NO_MATCH)


@pytest.mark.parametrize(
    ("facts", "lane_status", "message"),
    [
        pytest.param(None, ProviderStatus.CANDIDATES, "no owner link", id="no-link"),
        pytest.param(
            _facts("missing"),
            ProviderStatus.CANDIDATES,
            "outcome does not replay",
            id="missing-with-record",
        ),
        pytest.param(
            _facts("admitted", anchor=("Other Title", (LINKED_AUTHOR,))),
            ProviderStatus.CANDIDATES,
            "anchor does not replay",
            id="anchor-not-from-record",
        ),
        pytest.param(
            _facts("source_disabled"),
            ProviderStatus.NO_MATCH,
            "outcome does not replay",
            id="disabled-with-lane",
        ),
        pytest.param(
            _facts("medium_changed"),
            None,
            "medium is inconsistent",
            id="medium-unchanged",
        ),
        pytest.param(
            _facts("unavailable"),
            ProviderStatus.NO_MATCH,
            "outcome does not replay",
            id="unavailable-answered",
        ),
    ],
)
def test_owner_link_facts_must_replay(
    facts: OwnerLinkFacts | None, lane_status: ProviderStatus | None, message: str
) -> None:
    with pytest.raises(ReportValueError, match=message):
        _report(facts, lane_status=lane_status)


def test_a_disabled_source_reads_no_lane() -> None:
    report = _report(_facts("source_disabled"), lane_status=None)
    assert MetadataReport.from_json(report.to_json()).owner_link == report.owner_link


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"outcome": "missing", "reason": "format"}, id="reason"),
        pytest.param({"outcome": "contradicted"}, id="no-reason"),
        pytest.param(
            {"outcome": "missing", "anchor": (LINKED_TITLE, (LINKED_AUTHOR,))},
            id="anchor-unadmitted",
        ),
        pytest.param(
            {"anchor": (LINKED_TITLE, tuple(f"Author {n}" for n in range(5)))},
            id="too-many-creators",
        ),
        pytest.param({"anchor": (LINKED_TITLE, ())}, id="no-creators"),
        pytest.param({"provider": None}, id="work-without-source"),
        pytest.param({"identity": "0123"}, id="identity-grammar"),
        pytest.param({"outcome": "found"}, id="outcome"),
        pytest.param(
            {"scope": "edition", "provider": None, "identity": "9780306406157"},
            id="anchor-edition-ok",
        ),
    ],
)
def test_owner_link_facts_are_closed(changes: dict[str, object]) -> None:
    if changes.get("scope") == "edition":
        assert _facts(**changes).provider is None
        return
    with pytest.raises(ReportValueError):
        _facts(**changes)


def test_a_changed_digest_is_refused_on_readback() -> None:
    document = _report(_facts()).to_data()
    owner_link = document["owner_link"]
    assert isinstance(owner_link, dict)
    owner_link["digest"] = "0" * 64
    from mechanical_librarian.domain.metadata_report import canonical_json

    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(document))
