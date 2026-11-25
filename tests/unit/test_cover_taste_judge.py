"""Which cover fields the taste judge sees, and with which covers (#1372 PR 4).

Every field is prepared by the real producers: a report, its proposal and
case, and the cover consideration the Covers page reads. The judge then runs
against the real store with a fake engine, so J1's eligibility and J2's
candidate set are read from what the judge was actually sent.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_taste_judge import (
    CoverTasteJudgeService,
    JudgeDue,
    judge_candidates,
)
from mechanical_librarian.application.cover_taste_judge_contract import (
    AdmittedTasteJudgeRequest,
    TasteJudgeReply,
)
from mechanical_librarian.application.review_web import RecordHumanDecision
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.cover_rules import (
    COLOUR_BINS,
    LAYOUT_VALUES,
    CoverMeasures,
)
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_PROFILE_SCHEMA,
    TASTE_JUDGE_MAX_ALTERNATIVES,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.cover_producers import (
    _preview,
    _qualified_cover_report,
    incumbent_identity,
)
from tests.support.cover_taste import judge_profile
from tests.support.owner_field_record import insert_record_row, mark_cover_taken_over

NOW = 10_000
SAME_ART = CoverMeasures("0" * 16, "0" * 16, (1,) * COLOUR_BINS, (0,) * LAYOUT_VALUES)
# Each work's options for `_qualified_cover_report`, with `extra` more arts,
# `drop_candidate` to leave only the current art, and `hold` for a current
# cover whose identity contradicts the read (the stage holds).
WORKS: dict[str, dict[str, object]] = {
    # J1 a: a fault-free keep of a readable cover beside another art.
    "keep": {},
    # Twelve provider arts, the most a report can carry: the cap is reached.
    "keep_many": {"extra": 11},
    # J1 b: a fault the stage would cure.
    "wrong_shape": {"incumbent_size": (600, 600), "extra": 1},
    "below_floor": {
        "incumbent_size": (200, 300),
        "incumbent_duplicate_size": (2000, 3000),
    },
    "unreadable": {
        "readable_current": False,
        "abs_state": CoverSourceState.DECODE_FAILED,
        "extra": 1,
    },
    # Due by the statement, but the full report leaves one alternative.
    "larger_copy_alone": {
        "incumbent_size": (200, 300),
        "incumbent_duplicate_size": (2000, 3000),
        "drop_candidate": True,
    },
    # Never due.
    "same_art": {"shared_measures": SAME_ART},
    # A sharper copy of the current art is one art: branch b needs two.
    "sharper_copy_alone": {
        "incumbent_size": (400, 600),
        "candidate_size": (1200, 1800),
        "shared_measures": SAME_ART,
    },
    "unknown_read": {
        "readable_current": False,
        "abs_state": CoverSourceState.TIMEOUT,
    },
    "fault_one_art": {"incumbent_size": (600, 600), "drop_candidate": True},
    "fill": {
        "readable_current": False,
        "target_state": TargetFieldState.EMPTY,
        "extra": 1,
    },
    "hold": {"hold": True},
    "owner_decided": {},
    "held_for_owner": {},
    "released_hold": {},
}
JUDGED = {
    "keep",
    "keep_many",
    "wrong_shape",
    "below_floor",
    "unreadable",
    "released_hold",
}
SKIPPED = {"larger_copy_alone"}


def _extra(root: Path, index: int) -> CoverSource:
    artifacts = CoverArtifactStore(root / "artifacts")
    payload = _preview(40 + index)
    original = f"judge-extra-{index}".encode()
    artifact = CoverArtifact.create(
        original, payload, "image/png", 320 + index, 480 + index
    )
    artifacts.stage(artifact, original, payload)
    # A report holds at most eight Hardcover covers and twelve in all.
    kind = CoverSourceKind.HARDCOVER if index < 7 else CoverSourceKind.INVENTAIRE
    return CoverSource(
        cover_source_id(kind, f"judge-extra-{index}"),
        kind,
        CoverSourceState.RETRIEVED,
        artifact,
        provenance=CoverProvenance(
            kind.value, CoverDesignation.EXACT_EDITION, "work-3", "edition-3"
        ),
    )


def _report(root: Path, item: str, options: dict[str, object]) -> MetadataReport:
    options = dict(options)
    options.pop("hold", None)
    extra = cast(int, options.pop("extra", 0))
    drop_candidate = options.pop("drop_candidate", False)
    base, _ = _qualified_cover_report(root, **options)  # type: ignore[arg-type]  # reason: the options are the producer's own keywords
    sources = [
        source
        for source in base.cover_sources
        if not drop_candidate
        or source.source_id != cover_source_id(CoverSourceKind.HARDCOVER, "source-3")
    ]
    sources.extend(_extra(root, index) for index in range(extra))
    return MetadataReport.create(
        target=replace(base.target, item=item),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        cover_sources=tuple(sorted(sources, key=lambda source: source.source_id)),
    )


@dataclass
class _Library:
    repository: SQLiteMetadataReportRepository
    artifacts: CoverArtifactStore
    service: CoverConsiderationService
    cases: dict[str, str]
    considerations: dict[str, dict[str, Any]] = field(default_factory=dict)

    def item(self, consideration_id: str) -> str:
        row = self.repository._connection.execute(
            "SELECT json_extract(currentness_document,'$.item') "
            "FROM cover_considerations WHERE consideration_id=?",
            (consideration_id,),
        ).fetchone()
        return cast(str, row[0])


def _library(root: Path, works: dict[str, dict[str, object]]) -> _Library:
    repository = open_repository(root / "application.sqlite3", cross_thread=True)
    artifacts = CoverArtifactStore(root / "artifacts")
    reports = {item: _report(root, item, options) for item, options in works.items()}
    previews = {
        source.artifact.artifact_id: artifacts.preview(source.artifact)
        for report in reports.values()
        for source in report.cover_sources
        if source.artifact is not None
    }
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact_id, _variant: previews[artifact_id],
        read_preview_digest=artifacts.read_preview_digest,
        require_artifact=artifacts.require_artifact,
        clock=lambda: 10,
        bind_policy=repository.bind_current_cover_authority,
        retained_original=repository.retained_cover_original,
        require_retained=lambda _digest: None,
        provider_fetching=lambda _report: False,
    )
    repository.bind_cover_consideration_currentness(lambda *_facts: False)
    repository.bind_owner_cover_validation(service.require_owner_cover)
    library = _Library(repository, artifacts, service, {})
    for item, report in reports.items():
        hold = works[item].get("hold")
        with incumbent_identity("conflict") if hold else nullcontext():
            repository.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            repository.publish_proposal(proposal_set)
            case_id = json.loads(repository.admit_review_case(proposal_set))[
                "review_case_id"
            ]
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            )
            proposal = next(
                row for row in case.proposals if row["destination"] == "cover"
            )
            ensured = service.ensure(case_id, report.to_json(), proposal)
            library.considerations[item] = json.loads(
                repository._connection.execute(
                    "SELECT document FROM cover_considerations WHERE consideration_id=?",
                    (ensured["consideration_id"],),
                ).fetchone()[0]
            )
        library.cases[item] = case_id
    service.refresh_authority()
    return library


def _decide(library: _Library, item: str) -> None:
    """The owner chooses another cover through the real decision command."""
    repository = library.repository
    case_id = library.cases[item]
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(row for row in case.proposals if row["destination"] == "cover")
    consideration = library.considerations[item]
    chosen = next(
        candidate
        for candidate in consideration["candidates"]
        if not candidate["incumbent"] and candidate["selectable"]
    )
    RecordHumanDecision(repository, lambda: 50).run(
        case_id,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="cover",
        actor="owner",
        outcome="accept",
        selected_value={"kind": "cover", "value": chosen["artifact_id"]},
        idempotency_key="judge-owner-cover-answer-0001",
        consideration_id=str(consideration["consideration_id"]),
        cover_confirmation={
            "expected_selection_id": None,
            "confirm_unverified_match": True,
        },
    )


def _hold_for_owner(library: _Library, item: str, active: bool) -> None:
    """The record holds an owner's cover for the item that took it over from
    Automatic, with no answer on the item's own case, as one carried from an
    earlier case does (ADR-0108, S9). A completed Resume deletes the row, so
    a released field has none."""
    if active:
        _took_over_for_owner(library, item)


def _took_over_for_owner(library: _Library, item: str) -> None:
    """Point the item's record cover row at the owner's one cover choice,
    marked as taking the cover over from Automatic."""
    connection = library.repository._connection
    (selection,) = connection.execute(
        "SELECT selection_id FROM owner_cover_selections"
    ).fetchone()
    # The owner's real choice took nothing over.
    mark_cover_taken_over(connection, selection)
    insert_record_row(
        connection,
        library.cases[item],
        "cover",
        answer_id="human_decision_" + "7" * 64,
        cover_selection_id=selection,
    )


def _version(repository: SQLiteMetadataReportRepository, created_at: int = 0) -> int:
    """Append one owner-edited profile version holding `judge_profile`'s rules."""
    connection = repository._connection
    version = connection.execute(
        "SELECT coalesce(max(version),0)+1 FROM cover_taste_profile_versions"
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO cover_taste_profile_versions VALUES(?,NULL,?,?)",
        (
            version,
            created_at,
            canonical_json(
                {
                    "removed": [],
                    "rules": [rule.to_data() for rule in judge_profile().rules],
                    "schema_version": COVER_TASTE_PROFILE_SCHEMA,
                    "source": "edit",
                    "version": version,
                }
            ),
        ),
    )
    return cast(int, version)


class _Client:
    """Keeps a current cover, else chooses the first alternative it was sent."""

    def __init__(self) -> None:
        self.requests: list[AdmittedTasteJudgeRequest] = []
        self.closed = 0

    def run_taste_judge(
        self, _profile: bytes, request: AdmittedTasteJudgeRequest
    ) -> TasteJudgeReply:
        self.requests.append(request)
        if request.current is not None:
            return TasteJudgeReply(
                canonical_json(
                    {
                        "candidate": None,
                        "confidence": 90,
                        "decision": "keep_current",
                        "rules": [1],
                    }
                ),
                True,
            )
        return TasteJudgeReply(
            canonical_json(
                {
                    "candidate": request.images[0][0],
                    "confidence": 60,
                    "decision": "choose",
                    "rules": [2],
                }
            ),
            True,
        )

    def close(self) -> None:
        self.closed += 1


class _Access:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def current_taste_judge_model(self) -> str | None:
        return "example/vision"

    def open_taste_judge(self):
        return "openrouter", "example/vision", b"profile", self.client


def _judge(library: _Library, client: _Client) -> CoverTasteJudgeService:
    store = library.repository.cover_taste_judge_store()
    return CoverTasteJudgeService(
        lambda: store,
        _Access(client),  # type: ignore[arg-type]  # reason: the two methods the service reads
        library.artifacts.read_preview_digest,
        daily_limit=50,
        clock=lambda: NOW,
    )


def _pass(service: CoverTasteJudgeService) -> int:
    """Wake until nothing more is due; return how many fields were judged or
    skipped, one per wake."""
    handled = 0
    while service.wake(NOW):
        handled += 1
        assert handled <= len(WORKS)
    return handled


def _attempts(library: _Library) -> dict[str, tuple[str, str | None]]:
    return {
        library.item(consideration_id): (state, failure)
        for consideration_id, state, failure in library.repository._connection.execute(
            "SELECT consideration_id,state,failure FROM cover_taste_judgements"
        )
    }


@pytest.fixture(scope="module")
def judged(tmp_path_factory: pytest.TempPathFactory) -> tuple[_Library, _Client]:
    library = _library(tmp_path_factory.mktemp("judge"), WORKS)
    _decide(library, "owner_decided")
    _hold_for_owner(library, "held_for_owner", True)
    _hold_for_owner(library, "released_hold", False)
    _version(library.repository)
    client = _Client()
    assert _pass(_judge(library, client)) == len(JUDGED) + len(SKIPPED)
    return library, client


def test_each_branch_is_judged_once_and_every_exclusion_is_not(judged) -> None:
    """J1: branches a and b are judged; HOLD, FILL, a same-art pair, an
    unknown read, a fault with no other art, a sharper copy of the current
    art alone, an owner's answer and an
    active owner hold are not. A field the full report leaves with one
    alternative is recorded `skipped` once, with no request."""
    library, client = judged
    attempts = _attempts(library)
    assert attempts == {
        **{item: ("succeeded", None) for item in JUDGED},
        **{item: ("skipped", None) for item in SKIPPED},
    }
    assert len(client.requests) == len(JUDGED)
    assert client.closed == len(JUDGED)
    # The skipped and judged pairs are never selected again under this version.
    assert _pass(_judge(library, _Client())) == 0
    assert library.repository._connection.execute(
        "SELECT requests FROM reviewer_task_requests WHERE task='cover_taste_judge'"
    ).fetchall() == [(len(JUDGED),)]


def _sent(library: _Library, client: _Client) -> dict[str, list[dict[str, Any]]]:
    """Each judged item's covers as sent: the consideration's own candidates."""
    sent: dict[str, list[dict[str, Any]]] = {}
    for request in client.requests:
        real = dict(request.references)
        packet = json.loads(request.packet_document)
        consideration_id = next(
            consideration["consideration_id"]
            for consideration in library.considerations.values()
            if {candidate["reference"] for candidate in consideration["candidates"]}
            >= set(real.values())
        )
        item = library.item(cast(str, consideration_id))
        candidates = {
            candidate["reference"]: candidate
            for candidate in library.considerations[item]["candidates"]
        }
        sent[item] = [
            {**candidates[real[entry["reference"]]], "sent_current": entry["current"]}
            for entry in packet["candidates"]
        ]
    return sent


def _groups(library: _Library, item: str) -> dict[str, str]:
    stage = json.loads(library.considerations[item]["policy_report_document"])["stage"]
    return {
        member: group["representative"]
        for group in stage["groups"]
        for member in group["members"]
    }


def test_the_candidate_set_is_one_cover_of_each_art_beside_the_current(
    judged,
) -> None:
    """J2: the current cover is marked when readable; an art's representative
    stands for it; the current art is offered only as a resolution fault's
    cure; alternatives stop at 12."""
    library, client = judged
    sent = _sent(library, client)
    assert set(sent) == JUDGED
    for item, covers in sent.items():
        current = [cover for cover in covers if cover["sent_current"]]
        if item == "unreadable":
            assert current == []
        else:
            [only] = current
            assert only["incumbent"] is True
        groups = _groups(library, item)
        arts = [groups.get(cover["reference"], cover["reference"]) for cover in covers]
        assert len(set(arts)) == len(arts) or item == "below_floor", item
        for cover in covers:
            if not cover["sent_current"]:
                assert cover["candidate_kind"] in {"candidate", "larger_copy"}
                assert cover["preview_sha256"] and cover["transport_sha256"]

    # Branch a: no copy of the current art; another art is its representative.
    keep = sent["keep"]
    assert [cover["sent_current"] for cover in keep].count(False) == 1
    # Branch b, an art fault: the current art offers no alternative.
    wrong_shape = sent["wrong_shape"]
    groups = _groups(library, "wrong_shape")
    incumbent = library.considerations["wrong_shape"]["incumbent"]["reference"]
    current_art = groups.get(incumbent, incumbent)
    assert all(
        groups.get(cover["reference"], cover["reference"]) != current_art
        or cover["sent_current"]
        for cover in wrong_shape
    )
    assert len(wrong_shape) == 3
    # Branch b, a resolution fault: the larger copy cures the current art.
    below_floor = sent["below_floor"]
    assert [
        cover["candidate_kind"] for cover in below_floor if not cover["sent_current"]
    ].count("larger_copy") == 1
    assert len(below_floor) == 3
    # Every acceptable other art, in rank order, and nothing unacceptable.
    many = library.considerations["keep_many"]
    policy = json.loads(many["policy_report_document"])
    alternatives = [cover for cover in sent["keep_many"] if not cover["sent_current"]]
    assert {cover["reference"] for cover in alternatives} == set(
        policy["acceptable_candidate_references"]
    ) - {many["incumbent"]["reference"]}
    assert len(alternatives) > 2


def test_alternatives_stop_at_twelve_in_rank_order(judged) -> None:
    """J2's cap. A report holds at most twelve provider covers, so a real
    field never reaches it: the keep_many field's stored consideration is
    widened with more ranked copies of its own arts under new references."""
    library, _client = judged
    stored = library.considerations["keep_many"]
    stage = json.loads(stored["policy_report_document"])["stage"]
    due = JudgeDue(
        str(stored["consideration_id"]),
        library.cases["keep_many"],
        stored,
        "epub",
        stage["outcome"],
        stage["reason"],
    )
    consideration = dict(due.consideration)
    policy = json.loads(cast(str, consideration["policy_report_document"]))
    others = [
        candidate
        for candidate in cast("list[dict[str, Any]]", consideration["candidates"])
        if candidate["reference"] in policy["acceptable_candidate_references"]
        and not candidate["incumbent"]
    ]
    widened = [
        {
            **candidate,
            "reference": f"cover_ref_{index:064x}",
            "deterministic_rank": 100 + index,
            "ordinal": 100 + index,
        }
        for index, candidate in enumerate(others * 3)
    ]
    consideration["candidates"] = [
        *cast("list[dict[str, Any]]", consideration["candidates"]),
        *widened,
    ]
    policy["acceptable_candidate_references"] = [
        *policy["acceptable_candidate_references"],
        *(candidate["reference"] for candidate in widened),
    ]
    consideration["policy_report_document"] = json.dumps(policy)
    candidates = judge_candidates(replace(due, consideration=consideration))
    assert candidates is not None
    alternatives = [candidate for candidate in candidates if not candidate.current]
    assert len(alternatives) == TASTE_JUDGE_MAX_ALTERNATIVES
    ranked = sorted(
        (*others, *widened), key=lambda candidate: candidate["deterministic_rank"]
    )
    assert [candidate.reference for candidate in alternatives] == [
        candidate["reference"] for candidate in ranked[:TASTE_JUDGE_MAX_ALTERNATIVES]
    ]


def test_judge_candidates_leaves_the_stage_standing_when_too_few_remain(
    judged,
) -> None:
    library, _client = judged
    store = library.repository.cover_taste_judge_store()
    # A fresh version makes every never-judged eligible field due again.
    version = _version(library.repository, created_at=1)
    seen: dict[str, object] = {}
    for consideration_id in store.due(version, 64):
        due = store.due_one(consideration_id, version)
        assert due is not None
        item = library.item(due.consideration_id)
        seen[item] = judge_candidates(due)
        # A stage that no longer matches the statement's facts is not judged.
        assert judge_candidates(replace(due, reason="edition_preference")) is None
        store.record(due.consideration_id, version, NOW)
    assert store.due(version, 64) == ()
    # A same-art sharper copy on its own is never selected.
    assert "sharper_copy_alone" not in seen
    assert seen["larger_copy_alone"] is None
    # keep_current verdicts are never judged again under a newer version (S22).
    assert not {"keep", "keep_many", "wrong_shape", "below_floor"} & set(seen)
    # The unreadable field's verdict chose: it is judged again, first.
    assert "unreadable" in seen
