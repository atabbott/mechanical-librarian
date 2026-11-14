"""Cover identity, policy and consideration at a Book matching level (#1355).

S8 and S9: at `identifier` every outcome is today's and only level facts are
added; at a configured level as wide as the one a record meets, the outcome is
`title_creator_work_match`, which an owner may choose by one tap and which
never elects a cover automatically.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mechanical_librarian.application import cover_identity
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_identity import (
    _CLASSIFIER_RULES,
    _classify_sources,
    classify_cover_identity,
)
from mechanical_librarian.application.cover_packets import prepare_cover_packet
from mechanical_librarian.application.cover_policy import (
    CoverPolicy,
    adjudicate_cover_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    Comparison,
    MetadataReport,
    ProviderQueryKind,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.unit.test_cover_consideration import _Store
from tests.unit.test_cover_identity_listed_edition import (
    _candidate,
    _claim,
    _default_source,
    _isbn_lane_report,
    _listed_source,
    _report,
    _target,
)
from tests.unit.test_cover_packets import _preview

_IDENTIFIER = WorkIdentityLevel.IDENTIFIER
_EXACT = WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
_WIDE = WorkIdentityLevel.TITLE_AND_CREATOR
_MET_EXACT = ["work_identity_level_met", "work_identity:title_and_creator_exact"]


def _work_lane_report(*candidates: Any, **values: Any) -> MetadataReport:
    report = _report(*candidates, **values)
    cast("Any", report).providers[0].query_kind = ProviderQueryKind.WORK
    return report


# --- 6. Cover identity -------------------------------------------------------


def test_set_admitted_work_lane_candidate_gets_level_cover_outcome() -> None:
    """A WORK-lane record set admission accepted is evaluated by the seam."""
    report = _work_lane_report(_candidate())
    sources = (_default_source(),)
    assert _classify_sources(report, sources, _IDENTIFIER) == (
        "unknown",
        ["retained_title_evidence_insufficient", *_MET_EXACT],
        [],
    )
    for level in (_EXACT, _WIDE):
        assert _classify_sources(report, sources, level) == (
            "title_creator_work_match",
            ["retained_title_evidence_insufficient", *_MET_EXACT],
            [],
        )


def test_a_plausible_record_keeps_its_outcome_at_identifier_and_matches_wider() -> None:
    agreement = Comparison.AGREEMENT
    record = _candidate(
        extra=(),
    )
    record.claims = (
        _claim(ClaimKind.TITLE, "Synthetic Title", links=(_link(agreement),)),
        *record.claims[1:],
    )
    report = _report(record)
    identifier = _classify_sources(report, (_default_source(),), _IDENTIFIER)
    assert identifier[0] == "plausible_match"
    assert identifier[1][-2:] == _MET_EXACT
    wide = _classify_sources(report, (_default_source(),), _WIDE)
    assert wide[0] == "title_creator_work_match"
    assert wide[1] == identifier[1]


def _link(outcome: Comparison) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(outcome=outcome)


def test_level_offered_fold_pair_carries_no_contradiction_reason() -> None:
    """The #1790 rule: respelled initials are no creator disagreement."""
    disagreement = _link(Comparison.DISAGREEMENT)
    record = _candidate(creators=("R.F. Example",), creator_links=(disagreement,))
    report = _report(record, target=_target(authors=("R. F. Example",)))
    # The item states the author; the record respells the initials.
    cast("Any", report).local_claims = (
        SimpleNamespace(
            kind=ClaimKind.CREATOR, observed="R. F. Example", canonical="R. F. Example"
        ),
    )
    identifier = _classify_sources(report, (_listed_source(),), _IDENTIFIER)
    assert identifier == (
        "unknown",
        [
            "retained_creator_or_contributor_disagreement",
            *_MET_EXACT,
            "creator_initials_folded",
        ],
        [],
    )
    assert _classify_sources(report, (_listed_source(),), _EXACT) == (
        "title_creator_work_match",
        [*_MET_EXACT, "creator_initials_folded"],
        [],
    )


def test_a_contradicted_record_keeps_its_disagreement_at_every_level() -> None:
    disagreement = _link(Comparison.DISAGREEMENT)
    record = _candidate(creators=("Other Author",), creator_links=(disagreement,))
    report = _report(record)
    for level in WorkIdentityLevel:
        assert _classify_sources(report, (_listed_source(),), level) == (
            "unknown",
            [
                "retained_creator_or_contributor_disagreement",
                "work_identity_level_refused",
            ],
            [],
        )


def test_two_books_meeting_the_level_in_one_set_are_ambiguous() -> None:
    from types import SimpleNamespace

    first = _candidate(identity="record-1")
    second = _candidate(identity="record-2")
    second.work_identity = "hardcover-work-0000000008"
    lane = SimpleNamespace(
        kind=ProviderQueryKind.WORK,
        candidates=tuple(
            SimpleNamespace(
                edition_identity=value.identity, work_identity=value.work_identity
            )
            for value in (first, second)
        ),
    )
    report = _report(first, second, lanes=(lane,))
    assert _classify_sources(report, (_listed_source(),), _WIDE) == (
        "unknown",
        ["work_identity_level_ambiguous"],
        [],
    )


def test_the_level_rules_rotate_the_classifier_reference() -> None:
    assert {
        "listed-edition-work-identity-binding:level-outcome",
        "provider-identity-without-identifier-level-predicate:title-creator-work-match",
        "provider-identity-without-identifier-level-refused-or-ambiguous:unchanged",
    } <= set(_CLASSIFIER_RULES)
    assert not any("level-shadow" in rule for rule in _CLASSIFIER_RULES)
    assert cover_identity.SCHEMA_VERSION == "cover-identity-report-v2"


def _listed_document(level: WorkIdentityLevel) -> tuple[MetadataReport, Any, Any]:
    source = _listed_source(11)
    payload = _preview(11)
    report = _isbn_lane_report(source)
    packet = prepare_cover_packet(report.to_json(), lambda *_: payload)
    images = tuple(
        (value.reference, value.digest, value.payload) for value in packet.images
    )
    return report, packet, images


@pytest.mark.parametrize("level", list(WorkIdentityLevel))
def test_the_identity_document_records_its_level(level: WorkIdentityLevel) -> None:
    report, packet, images = _listed_document(level)
    document = json.loads(
        classify_cover_identity(
            report.to_json(),
            packet.document,
            images,
            read_preview=lambda *_: _preview(11),
            work_identity_level=level,
        ).document
    )
    assert document["work_identity_level"] == level.value
    (row,) = document["candidates"]
    assert row["outcome"] == (
        "unknown" if level is _IDENTIFIER else "title_creator_work_match"
    )
    assert row["reasons"] == sorted(_MET_EXACT)


# --- 7. Cover policy ---------------------------------------------------------


def test_the_cover_policy_accepts_a_level_match_and_embeds_the_level() -> None:
    report, packet, images = _listed_document(_EXACT)

    def adjudicate(level: WorkIdentityLevel) -> dict[str, Any]:
        return json.loads(
            adjudicate_cover_policy(
                report.to_json(),
                packet.document,
                images,
                None,
                CoverPolicy(),
                read_preview=lambda *_: _preview(11),
                work_identity_level=level,
            ).document
        )

    identity = classify_cover_identity(
        report.to_json(),
        packet.document,
        images,
        read_preview=lambda *_: _preview(11),
        work_identity_level=_EXACT,
    ).document
    wide, narrow = adjudicate(_EXACT), adjudicate(_IDENTIFIER)
    # The level reaches the policy report through its identity document.
    assert (
        wide["identity_report_sha256"]
        == hashlib.sha256(identity.encode("utf-8")).hexdigest()
    )
    assert wide["identity_report_sha256"] != narrow["identity_report_sha256"]
    assert wide["schema_version"] == "cover-policy-report-v3"
    # A level match never elects: nothing is recommended at either level.
    assert wide["recommended_candidate_reference"] is None
    assert narrow["recommended_candidate_reference"] is None


# --- 8. Consideration ---------------------------------------------------------


def _service(level: WorkIdentityLevel) -> CoverConsiderationService:
    payload = _preview(11)
    return CoverConsiderationService(
        _Store(),
        read_preview=lambda *_: payload,
        read_preview_digest=lambda _: payload,
        clock=lambda: 10,
        work_identity_level=level,
    )


def test_a_level_match_is_an_unconfirmed_owner_choice_said_by_title_and_author() -> (
    None
):
    report = _isbn_lane_report(_listed_source(11))
    service = _service(_EXACT)
    case_id = "review_case_" + "c" * 64
    projection = service.ensure(
        case_id, report.to_json(), {"proposal_id": "review_proposal_" + "d" * 64}
    )
    (candidate,) = cast("list[dict[str, Any]]", projection["candidates"])
    assert candidate["identity"]["outcome"] == "title_creator_work_match"
    assert candidate["match_state"] == "match_not_confirmed"
    assert candidate["match_reason"] == "title_and_author"
    assert candidate["manual_eligible"] is True
    assert candidate["selectable"] is False
    assert candidate["deterministic_rank"] is None
    assert candidate["deterministic_rank_key"] is None
    assert "would_admit" not in projection
    service.validate_decision(
        case_id,
        str(projection["consideration_id"]),
        "accept",
        {"kind": "cover", "value": candidate["artifact_id"]},
    )


def test_would_admit_is_stored_only_when_the_shadow_election_differs() -> None:
    report = _isbn_lane_report(_listed_source(11))
    case_id = "review_case_" + "c" * 64
    proposal: dict[str, object] = {"proposal_id": "review_proposal_" + "d" * 64}
    stored = {}
    for level in (_IDENTIFIER, _EXACT):
        service = _service(level)
        service.ensure(case_id, report.to_json(), proposal)
        store = cast("Any", service)._store
        stored[level] = json.loads(store.current[case_id])
    assert stored[_IDENTIFIER]["would_admit"] is None
    would_admit = stored[_EXACT]["would_admit"]
    assert would_admit is not None
    assert set(would_admit) == {"candidate_reference", "rule"}
    assert would_admit["rule"] == "work_identity:title_and_creator_exact"
    # The live recommendation is unchanged.
    assert (
        json.loads(stored[_EXACT]["policy_report_document"])[
            "recommended_candidate_reference"
        ]
        == json.loads(stored[_IDENTIFIER]["policy_report_document"])[
            "recommended_candidate_reference"
        ]
    )


def test_no_review_api_view_carries_would_admit() -> None:
    """Stored, never shown until #1418 (A1)."""
    from pathlib import Path

    openapi = (
        Path(__file__).parents[2] / "docs" / "reference" / "review-openapi-v1.json"
    ).read_text(encoding="utf-8")
    assert "title_and_author" in openapi
    assert "would_admit" not in openapi


# --- 11. Staleness -----------------------------------------------------------


def _reviewing_service(
    case: Any,
    level: WorkIdentityLevel,
    dispatches: list[str],
    *,
    fetching: bool = False,
) -> CoverConsiderationService:
    from tests.unit.test_cover_consideration import (
        ReviewerDispatch,
        ReviewRunner,
        _observer_result,
    )

    profile = b"openrouter-profile"

    def reviewer(*args: object) -> str:
        dispatches.append("openrouter")
        return _observer_result(*cast("Any", args))

    return CoverConsiderationService(
        case.store,
        read_preview=lambda artifact, _variant: case.payload_by_artifact[artifact],
        read_preview_digest=lambda digest: case.payload_by_preview[digest],
        clock=lambda: case.now,
        reviewer_dispatch=lambda: ReviewerDispatch(
            7, "openrouter", "example/vision", profile, cast("ReviewRunner", reviewer)
        ),
        reviewer_identity=lambda: (7, "openrouter", "example/vision", profile),
        reviewer_revision_current=lambda revision: revision == 7,
        work_identity_level=level,
        provider_fetching=lambda _report: fetching,
    )


@pytest.mark.parametrize(
    ("first", "second"),
    ((_IDENTIFIER, _WIDE), (_WIDE, _IDENTIFIER)),
    ids=("widened", "back_to_identifier"),
)
def test_a_level_change_refreshes_the_comparison_without_a_reviewer_request(
    first: WorkIdentityLevel, second: WorkIdentityLevel
) -> None:
    from tests.unit.test_cover_consideration import _Case

    case = _Case()
    case_id = "review_case_" + "7" * 64
    dispatches: list[str] = []
    before = _reviewing_service(case, first, dispatches)
    original = before.ensure(case_id, case.report.to_json(), dict(case.proposal))
    assert dispatches == ["openrouter"]
    saved = json.loads(case.store.current[case_id])

    after = _reviewing_service(case, second, dispatches)
    # The saved comparison names the other level, so it is no longer current.
    assert after.project(case.store.current[case_id]) is None
    successor = after.ensure(case_id, case.report.to_json(), dict(case.proposal))

    # Re-adjudicated from the stored observer output: no reviewer request.
    assert dispatches == ["openrouter"]
    assert successor["supersedes_id"] == original["consideration_id"]
    current = json.loads(case.store.current[case_id])
    assert current["observation_document"] == saved["observation_document"]
    assert current["reviewer_status"] == saved["reviewer_status"]
    assert json.loads(current["identity_document"])["work_identity_level"] == (
        second.value
    )
    assert current["replay_reference"] != saved["replay_reference"]


def test_a_comparison_made_while_sources_were_fetching_is_reviewed_afresh() -> None:
    """A comparison made while providers were still fetching never had the
    observer review that follows fetching; a level change must not reuse its
    output in place of that review (reviewer observation 1)."""
    from tests.unit.test_cover_consideration import _Case

    case = _Case()
    case_id = "review_case_" + "8" * 64
    dispatches: list[str] = []
    before = _reviewing_service(case, _IDENTIFIER, dispatches, fetching=True)
    before.ensure(case_id, case.report.to_json(), dict(case.proposal))
    assert json.loads(case.store.current[case_id])["provider_fetching"] is True
    requests = len(dispatches)

    after = _reviewing_service(case, _WIDE, dispatches)
    after.ensure(case_id, case.report.to_json(), dict(case.proposal))
    assert len(dispatches) == requests + 1
    current = json.loads(case.store.current[case_id])
    assert current["provider_fetching"] is False
    assert current["work_identity_level"] == "title_and_creator"


# --- 14. Persistence versions -------------------------------------------------


def test_the_new_documents_read_back_and_each_predecessor_is_refused() -> None:
    from mechanical_librarian.application.cover_consideration import (
        _validated_cover_consideration,
    )
    from mechanical_librarian.application.cover_packet_semantics import (
        canonical_packet_json as canonical_json_text,
    )
    from mechanical_librarian.application.cover_policy import _identity_rows

    report = _isbn_lane_report(_listed_source(11))
    service = _service(_EXACT)
    case_id = "review_case_" + "c" * 64
    service.ensure(
        case_id, report.to_json(), {"proposal_id": "review_proposal_" + "d" * 64}
    )
    stored = cast("Any", service)._store.current[case_id]
    value = json.loads(stored)
    assert value["schema_version"] == "cover-consideration-v7"
    _validated_cover_consideration(stored)

    def refused(document: dict[str, Any]) -> None:
        with pytest.raises((ValueError, KeyError, TypeError)):
            _validated_cover_consideration(canonical_json_text(document))

    refused({**value, "schema_version": "cover-consideration-v6"})
    refused({key: item for key, item in value.items() if key != "would_admit"})
    # The level is recorded at top level and agrees with the identity document.
    assert value["work_identity_level"] == "title_and_creator_exact"
    refused({k: item for k, item in value.items() if k != "work_identity_level"})
    with pytest.raises(ValueError, match="cover consideration level is invalid"):
        _validated_cover_consideration(
            canonical_json_text({**value, "work_identity_level": "identifier"})
        )
    policy_v2 = {
        **json.loads(value["policy_report_document"]),
        "schema_version": "cover-policy-report-v2",
    }
    refused({**value, "policy_report_document": canonical_json_text(policy_v2)})

    identity = json.loads(value["identity_document"])
    packet = json.loads(value["packet_document"])
    references = tuple(row["reference"] for row in packet["candidates"])
    _identity_rows(
        value["identity_document"],
        packet["packet_reference"],
        packet["candidates"],
        references,
    )
    for previous in (
        {**identity, "schema_version": "cover-identity-report-v1"},
        {key: item for key, item in identity.items() if key != "work_identity_level"},
        {**identity, "work_identity_level": "exact"},
    ):
        with pytest.raises(ValueError, match="cover identity report is invalid"):
            _identity_rows(
                canonical_json_text(previous),
                packet["packet_reference"],
                packet["candidates"],
                references,
            )
    policy = json.loads(value["policy_report_document"])
    assert policy["schema_version"] == "cover-policy-report-v3"


def _shadow_candidate(
    reference: str, *, incumbent: bool, size: int, ordinal: int
) -> dict[str, object]:
    return {
        "candidate_kind": "candidate",
        "reference": reference,
        "incumbent": incumbent,
        "width": size,
        "height": size * 3 // 2,
        "ordinal": ordinal,
        "identity": {
            "outcome": "title_creator_work_match",
            "reasons": sorted(_MET_EXACT),
        },
    }


@pytest.mark.parametrize(
    ("cover_state", "expected"),
    ((TargetFieldState.VALUE, "alternative"), (TargetFieldState.MISSING, None)),
    ids=("incumbent_level_matched", "no_current_cover_value"),
)
def test_the_shadow_election_confirms_a_level_matched_incumbent(
    cover_state: TargetFieldState, expected: str | None
) -> None:
    """An incumbent that only a level matches is, for the shadow election, a
    confirmed current cover: a better level-matched alternative outranks it
    and is named, though the live policy says `unknown` (Codex r5, 1)."""
    from mechanical_librarian.application.cover_consideration import _would_admit
    from tests.unit.test_cover_identity_listed_edition import _target

    target = _target()
    fields = tuple(
        TargetField(field.destination, cover_state, "current-cover")
        if field.destination is TargetDestination.COVER
        and cover_state is TargetFieldState.VALUE
        else field
        for field in target.fields
    )
    report = cast(
        "MetadataReport",
        SimpleNamespace(
            audio=None,
            target=TargetFacts(
                target.connection, target.library, target.item, fields
            ),
        ),
    )
    candidates = [
        _shadow_candidate("current", incumbent=True, size=300, ordinal=0),
        _shadow_candidate("alternative", incumbent=False, size=600, ordinal=1),
    ]
    policy = json.dumps(
        {"actual_target_state": "unknown", "recommended_candidate_reference": None}
    )
    note = _would_admit(report, candidates, policy, provider_fetching=False)
    assert (None if note is None else note["candidate_reference"]) == expected
