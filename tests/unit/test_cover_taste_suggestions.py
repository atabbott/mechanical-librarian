"""The taste judge's suggestions as the owner reads them (#1372 PR 4b, J8, J9).

Fields are prepared by the real producers (`tests/unit/test_cover_taste_judge.py`)
and judged by the real service with a fake engine whose answer each test
chooses; Covers, the case detail and Taste profile then read the stored
verdicts through the real store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.cover_taste_build import (
    CoverTasteBuildService,
    CoverTasteRetryError,
)
from mechanical_librarian.application.cover_taste_inputs import assemble_taste_inputs
from mechanical_librarian.application.cover_taste_judge import CoverTasteJudgeService
from mechanical_librarian.application.cover_taste_judge_contract import (
    AdmittedTasteJudgeRequest,
    TasteJudgeReply,
)
from mechanical_librarian.application.review_covers import cover_page
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    RecordHumanDecision,
)
from mechanical_librarian.domain.cover_taste import TASTE_JUDGE_SETTLE_SECONDS
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.cover_taste import judge_profile
from tests.unit.test_cover_taste_judge import (
    WORKS,
    _Library,
    _library,
    _report,
    _version,
)

NOW = 1_000_000
# item -> (decision, confidence, rules); a missing item's check fails.
Plan = dict[str, tuple[str, int, list[int]]]


class _Client:
    def __init__(self, library: _Library, plan: Plan) -> None:
        self.library = library
        self.plan = plan

    def run_taste_judge(
        self, _profile: bytes, request: AdmittedTasteJudgeRequest
    ) -> TasteJudgeReply:
        real = {reference for _opaque, reference in request.references}
        item = next(
            item
            for item, consideration in self.library.considerations.items()
            if real
            <= {candidate["reference"] for candidate in consideration["candidates"]}
        )
        if item not in self.plan:
            # An unusable reply fails the check without pausing the pass.
            return TasteJudgeReply("not a verdict", True)
        decision, confidence, rules = self.plan[item]
        chosen = next(
            opaque for opaque, _real in request.references if opaque != request.current
        )
        return TasteJudgeReply(
            canonical_json(
                {
                    "candidate": chosen if decision == "choose" else None,
                    "confidence": confidence,
                    "decision": decision,
                    "rules": rules,
                }
            ),
            True,
        )

    def close(self) -> None:
        pass


class _Access:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def current_taste_judge_model(self) -> str | None:
        return "example/vision"

    def current_taste_build_model(self) -> str | None:
        return None

    def open_taste_judge(self):
        return "openrouter", "example/vision", b"profile", self.client


def _judge(library: _Library, plan: Plan, now: int = NOW) -> None:
    """Judge every due field once at `now`."""
    store = library.repository.cover_taste_judge_store()
    service = CoverTasteJudgeService(
        lambda: store,
        _Access(_Client(library, plan)),  # type: ignore[arg-type]  # reason: the methods the service reads
        library.artifacts.read_preview_digest,
        daily_limit=50,
        clock=lambda: now,
    )
    for _ in range(len(WORKS) + 1):
        if not service.wake(now):
            return
    raise AssertionError("the pass did not end")


def _taste(library: _Library, now: int = NOW) -> CoverTasteBuildService:
    store = library.repository.cover_taste_store()
    return CoverTasteBuildService(
        lambda: store,
        _Access(_Client(library, {})),  # type: ignore[arg-type]  # reason: the methods the service reads
        library.artifacts.read_preview_digest,
        daily_limit=8,
        judge_daily_limit=50,
        clock=lambda: now,
    )


def _choose(library: _Library, item: str, reference: str | None, at: int) -> None:
    """The owner keeps the current cover (None) or chooses `reference`."""
    repository = library.repository
    case_id = library.cases[item]
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(row for row in case.proposals if row["destination"] == "cover")
    consideration = library.considerations[item]
    chosen = next(
        (
            candidate
            for candidate in consideration["candidates"]
            if candidate["reference"] == reference
        ),
        None,
    )
    RecordHumanDecision(repository, lambda: at).run(
        case_id,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="cover",
        actor="owner",
        outcome="keep_current" if chosen is None else "accept",
        selected_value=None
        if chosen is None
        else {"kind": "cover", "value": chosen["artifact_id"]},
        idempotency_key=f"suggestion-answer-{item}-0001",
        consideration_id=str(consideration["consideration_id"]),
        cover_confirmation={
            "expected_selection_id": None,
            "confirm_unverified_match": chosen is not None,
        },
    )


def _suggested(library: _Library, item: str) -> str:
    """The real reference the newest verdict on the item's comparison chose."""
    row = library.repository._connection.execute(
        "SELECT json_extract(document,'$.candidate_reference') FROM cover_taste_verdicts "
        "WHERE consideration_id=? ORDER BY rowid DESC LIMIT 1",
        (library.considerations[item]["consideration_id"],),
    ).fetchone()
    return cast(str, row[0])


def _other(library: _Library, item: str, *not_these: str) -> str:
    return next(
        candidate["reference"]
        for candidate in library.considerations[item]["candidates"]
        if not candidate["incumbent"]
        and candidate["selectable"]
        and candidate["reference"] not in not_these
    )


def _judgements(library: _Library, item: str = "keep_many") -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in library.repository._connection.execute(
            "SELECT trigger,state,failure FROM cover_taste_judgements "
            "WHERE consideration_id=? ORDER BY rowid",
            (library.considerations[item]["consideration_id"],),
        )
    ]


def _detail_suggestion(library: _Library, item: str) -> object:
    detail = InspectCurrentReviewCase(library.repository, library.service).run(
        library.cases[item]
    )
    return next(
        row
        for row in cast("list[dict[str, object]]", detail["fields"])
        if row["destination"] == "cover"
    )["cover_taste_suggestion"]


def _decide(library: _Library) -> list[tuple[str, object, object]]:
    page = cover_page(library.repository, "decide", None)
    return [
        (
            next(
                item
                for item, case in library.cases.items()
                if case == row["review_case_id"]
            ),
            row["group"],
            row["suggestion"],
        )
        for row in cast("list[dict[str, object]]", page["items"])
    ]


@pytest.fixture
def library(tmp_path: Path):
    value = _library(
        tmp_path,
        {
            name: WORKS[name]
            for name in ("keep", "keep_many", "wrong_shape", "below_floor")
        },
    )
    _version(value.repository)
    try:
        yield value
    finally:
        value.repository.close()


def test_to_decide_lists_strong_then_likely_then_rule_suggestions(library) -> None:
    """J8: a fault-free keep the judge would switch moves from Kept to To
    decide; a fault cure keeps its fault and takes the judge's strength; the
    order is Strong, Likely, then rule suggestions with no verdict."""
    before = _decide(library)
    assert {item for item, _group, _suggestion in before} == {
        "wrong_shape",
        "below_floor",
    }
    kept = cover_page(library.repository, "kept", None)
    assert len(cast(list[object], kept["items"])) == 2
    _judge(
        library,
        {
            "keep": ("choose", 70, [1]),
            "keep_many": ("choose", 90, [1, 2, 3]),
            "wrong_shape": ("choose", 89, [2]),
        },
    )
    assert _decide(library) == [
        ("keep_many", "proposal", {"reason": None, "taste": {"strength": "strong"}}),
        ("keep", "proposal", {"reason": None, "taste": {"strength": "likely"}}),
        (
            "wrong_shape",
            "proposal",
            {"reason": "wrong_shape", "taste": {"strength": "likely"}},
        ),
        ("below_floor", "proposal", {"reason": "below_floor", "taste": None}),
    ]
    counts = cover_page(library.repository, "all", None)["counts"]
    assert counts == {"decide": 4, "kept": 0, "switched": 0, "all": 4}
    # Each page resumes strictly after the last row shown, across the ranks.
    store = library.repository
    first = store.read_review_covers("decide", 2, None)
    rest = store.read_review_covers("decide", 50, first.rows[-1].key)
    assert [row.taste_strength for row in (*first.rows, *rest.rows)] == [
        "strong",
        "likely",
        "likely",
        None,
    ]


def test_keep_and_abstain_change_nothing_and_an_answer_removes_a_suggestion(
    library,
) -> None:
    _judge(
        library,
        {
            "keep": ("keep_current", 95, [1]),
            "keep_many": ("abstain", 10, [1]),
            "wrong_shape": ("choose", 95, [2]),
            "below_floor": ("choose", 60, [2]),
        },
    )
    assert [item for item, _g, _s in _decide(library)] == ["wrong_shape", "below_floor"]
    assert _detail_suggestion(library, "wrong_shape") is not None
    assert _detail_suggestion(library, "below_floor") is not None
    _choose(library, "wrong_shape", _suggested(library, "wrong_shape"), NOW + 10)
    assert [item for item, _g, _s in _decide(library)] == ["below_floor"]
    # Once the owner answers, the work's detail drops the suggestion (B3);
    # keeping the current cover answers it too.
    assert _detail_suggestion(library, "wrong_shape") is None
    _choose(library, "below_floor", None, NOW + 20)
    assert _detail_suggestion(library, "below_floor") is None


def test_a_new_comparison_removes_a_suggestion(library, tmp_path: Path) -> None:
    """B1: a verdict belongs to the comparison it judged; once the work has a
    new comparison it suggests nothing until that one is judged."""
    _judge(library, {"keep": ("choose", 95, [1])})
    assert _decide(library)[0][0] == "keep"
    assert _detail_suggestion(library, "keep") is not None
    case_id = library.cases["keep"]
    before = _pointer(library, case_id)
    library.service.update_policy(CoverPolicy(prefer_edition=True))
    case = ReviewCase.create_from_data(
        json.loads(library.repository.get_review_case(case_id))["case"]
    )
    library.service.ensure(
        case_id,
        _report(tmp_path, "keep", WORKS["keep"]).to_json(),
        next(row for row in case.proposals if row["destination"] == "cover"),
    )
    assert _pointer(library, case_id) != before
    assert "keep" not in [item for item, _g, _s in _decide(library)]
    assert _detail_suggestion(library, "keep") is None


def _pointer(library: _Library, case_id: str) -> str:
    return cast(
        str,
        library.repository._connection.execute(
            "SELECT consideration_id FROM current_cover_considerations "
            "WHERE review_case_id=?",
            (case_id,),
        ).fetchone()[0],
    )


def test_a_verdict_under_an_older_version_still_shows_with_its_rules(library) -> None:
    """Q4: after an owner edit, a pending suggestion keeps showing, with the
    rule texts of the version it was judged under, until it is judged again."""
    _judge(library, {"keep": ("choose", 95, [3, 1])})
    case_id = library.cases["keep"]
    detail = InspectCurrentReviewCase(library.repository, library.service).run(case_id)
    field = next(
        row
        for row in cast("list[dict[str, object]]", detail["fields"])
        if row["destination"] == "cover"
    )
    rules = {rule.number: rule for rule in judge_profile().rules}
    suggestion = cast("dict[str, object]", field["cover_taste_suggestion"])
    assert suggestion["candidate_reference"] == _suggested(library, "keep")
    assert suggestion["strength"] == "strong"
    assert (suggestion["version"], suggestion["version_in_use"]) == (1, 1)
    assert suggestion["rules"] == [
        {"number": 3, "section": rules[3].section, "text": rules[3].text},
        {"number": 1, "section": rules[1].section, "text": rules[1].text},
    ]
    connection = library.repository._connection
    # An owner edit replaces rule 3's text in version 2.
    document = json.loads(
        connection.execute(
            "SELECT document FROM cover_taste_profile_versions WHERE version=1"
        ).fetchone()[0]
    )
    for rule in document["rules"]:
        if rule["number"] == 3:
            rule["text"] = "Prefer a quiet cover."
    connection.execute(
        "INSERT INTO cover_taste_profile_versions VALUES(2,NULL,0,?)",
        (canonical_json({**document, "version": 2}),),
    )
    detail = InspectCurrentReviewCase(library.repository, library.service).run(case_id)
    field = next(
        row
        for row in cast("list[dict[str, object]]", detail["fields"])
        if row["destination"] == "cover"
    )
    # The older version's rules, now beside the version in use (Q4).
    assert field["cover_taste_suggestion"] == {**suggestion, "version_in_use": 2}
    assert [item for item, _g, _s in _decide(library)][0] == "keep"
    # Judged again under version 2: the stored keep ends the suggestion.
    _judge(
        library, {"keep": ("keep_current", 80, [1])}, NOW + TASTE_JUDGE_SETTLE_SECONDS
    )
    detail = InspectCurrentReviewCase(library.repository, library.service).run(case_id)
    field = next(
        row
        for row in cast("list[dict[str, object]]", detail["fields"])
        if row["destination"] == "cover"
    )
    assert field["cover_taste_suggestion"] is None
    assert "keep" not in [item for item, _g, _s in _decide(library)]


def test_the_judge_state_and_try_again(library) -> None:
    """Taste profile reads failed checks under the version in use; Try again
    asks once more for each, and only then is each sent again."""
    taste = _taste(library)
    judge = cast("dict[str, object]", taste.read()["judge"])
    assert judge == {
        "configured": True,
        "daily_limit": 50,
        "stopped": None,
        "retry_after": None,
        "failed": 0,
    }
    # With nothing failed, Try again under the version in use asks for
    # nothing and still succeeds; under any other version it is stale.
    taste.retry_judge(1)
    with pytest.raises(CoverTasteRetryError):
        taste.retry_judge(2)
    assert _judgements(library) == []
    answered: Plan = {
        "keep": ("choose", 95, [1]),
        "wrong_shape": ("abstain", 10, [1]),
        "below_floor": ("abstain", 10, [1]),
    }
    _judge(library, answered)
    assert cast("dict[str, object]", taste.read()["judge"])["failed"] == 1
    # A failed pair is not sent again on its own.
    _judge(library, {"keep_many": ("choose", 95, [1])}, NOW + 1)
    assert cast("dict[str, object]", taste.read()["judge"])["failed"] == 1
    # A failed field counts only while it could be sent again: held for the
    # owner it is neither counted nor asked for.
    connection = library.repository._connection
    connection.execute(
        "INSERT INTO field_effect_manual_holds VALUES(?,?,?,?,?)",
        (library.cases["keep_many"], "cover", 1, 60, "{}"),
    )
    assert cast("dict[str, object]", taste.read()["judge"])["failed"] == 0
    taste.retry_judge(1)
    assert len(_judgements(library)) == 1
    connection.execute("DELETE FROM field_effect_manual_holds")
    assert cast("dict[str, object]", taste.read()["judge"])["failed"] == 1
    with pytest.raises(CoverTasteRetryError):
        taste.retry_judge(2)
    taste.retry_judge(1)
    assert cast("dict[str, object]", taste.read()["judge"])["failed"] == 0
    # A repeat, as after a lost answer, succeeds and asks for nothing more.
    taste.retry_judge(1)
    assert [row[:2] for row in _judgements(library)] == [
        ("cadence", "failed"),
        ("retry", "requested"),
    ]
    # A restart before it is sent leaves the waiting Try again waiting.
    library.repository.cover_taste_judge_store().interrupt_recovered(NOW + 1)
    assert [row[:2] for row in _judgements(library)] == [
        ("cadence", "failed"),
        ("retry", "requested"),
    ]
    _judge(library, {"keep_many": ("choose", 95, [1])}, NOW + 2)
    assert _judgements(library) == [
        ("cadence", "failed", "invalid_output"),
        ("retry", "succeeded", None),
    ]
    assert [item for item, _g, _s in _decide(library)][:2] == ["keep", "keep_many"]
    requests = library.repository._connection.execute(
        "SELECT requests FROM reviewer_task_requests WHERE task='cover_taste_judge'"
    ).fetchone()[0]
    assert requests == 5
    # At the ceiling Taste profile names the stop and when it lifts.
    stopped = cast("dict[str, object]", _taste(library).read()["judge"])
    assert stopped["stopped"] is None
    library.repository._connection.execute(
        "UPDATE reviewer_task_requests SET requests=50 WHERE task='cover_taste_judge'"
    )
    stopped = cast("dict[str, object]", _taste(library).read()["judge"])
    assert stopped["stopped"] == "ceiling"
    assert stopped["retry_after"] == (NOW // 86_400 + 1) * 86_400
    library.repository._connection.execute(
        "UPDATE reviewer_task_requests SET stopped='unknown_charge' "
        "WHERE task='cover_taste_judge'"
    )
    assert cast("dict[str, object]", _taste(library).read()["judge"])["stopped"] == (
        "unknown_charge"
    )


def test_confirmations_and_corrections(library, tmp_path: Path) -> None:
    """J9: a selection under a suggestion counts toward m, and toward n when
    it took the suggestion; a keep or another cover under a suggestion, or
    another cover under a keep, is a correction the builder sends first."""
    # A selection before any verdict is counted nowhere.
    _choose(library, "below_floor", None, NOW - 10)
    _judge(
        library,
        {
            "keep": ("choose", 95, [1]),
            "keep_many": ("choose", 70, [1]),
            "wrong_shape": ("keep_current", 70, [2]),
        },
    )
    later = NOW + 10
    _choose(library, "keep", _suggested(library, "keep"), later)
    _choose(library, "keep_many", None, later)
    current = next(
        candidate["reference"]
        for candidate in library.considerations["wrong_shape"]["candidates"]
        if candidate["incumbent"]
    )
    _choose(library, "wrong_shape", _other(library, "wrong_shape", current), later)
    assert _taste(library).read()["confirmed"] == {"confirmed": 1, "suggested": 2}
    decisions = library.repository.cover_taste_store().decisions()
    corrections = {
        next(
            item
            for item, consideration in library.considerations.items()
            if decision.chosen_reference
            in {candidate["reference"] for candidate in consideration["candidates"]}
        ): decision.correction
        for decision in decisions
    }
    assert corrections == {
        "below_floor": False,
        "keep": False,
        "keep_many": True,
        "wrong_shape": True,
    }
    inputs = assemble_taste_inputs(
        decisions,
        library.artifacts.read_preview_digest,
        kept=(),
        left_out=(),
        nonce=b"\0" * 32,
    )
    assert inputs is not None
    assert inputs.record["corrections"] == 2


def test_the_covers_read_finds_each_verdict_by_its_index(library) -> None:
    """R3: the taste suggestion is read per work through the verdict index."""
    from mechanical_librarian.adapters.persistence.review_covers import _WORKS_SQL

    policy, replay = library.repository._current_cover_authority
    plan = " ".join(
        row[-1]
        for row in library.repository._connection.execute(
            "EXPLAIN QUERY PLAN " + _WORKS_SQL + "SELECT * FROM work",
            (policy, replay) * 3,
        )
    )
    assert "SEARCH verdict USING INTEGER PRIMARY KEY (rowid=?)" in plan
    assert (
        "SEARCH newest USING COVERING INDEX cover_taste_verdicts_consideration "
        "(consideration_id=?)" in plan
    )
    assert "SCAN verdict" not in plan and "SCAN newest" not in plan
