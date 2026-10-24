"""A row's human decisions are validated in one projection of its case (#1215).

Library, search, the attention lens and the case detail used to project a
case's proposals once per decision, in the store and again in the row
projection. The refusals below were captured from that code at base a1d73710,
verified unchanged at da64015c, and are pinned here, outer error and whole
cause chain, so the single projection is held to every refusal the repeated
one raised, and in the same order.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.domain import human_decision, review_case
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    admitted_applications,
    decision_actions,
)
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
    proposal_media_kind,
    review_proposals,
)
from tests.support.owner_responsiveness import OwnerResponsivenessFixture

_REVISIONS = 3
_DESTINATIONS = ("title", "description", "isbn")


def _decide(
    fixture: OwnerResponsivenessFixture, case: ReviewCase, revisions: int, tag: str
) -> None:
    """Record every revision through the repository; all but the last are superseded."""
    proposals = [
        value
        for value in case.proposals
        if value["destination"] in _DESTINATIONS
        and "keep_current" in decision_actions(value)
    ]
    assert proposals
    for revision in range(revisions):
        for proposal in proposals:
            fixture.repository.record_human_decision(
                HumanDecision.create(
                    case,
                    proposal_id=str(proposal["proposal_id"]),
                    target_fingerprint=case.target_fingerprint,
                    destination=str(proposal["destination"]),
                    actor="owner",
                    outcome="keep_current",
                    selected_value=None,
                    idempotency_key=f"scope-{tag}-{revision}-{proposal['destination']}",
                    occurred_at=1_000 + revision,
                )
            )


@contextmanager
def _decided(tmp_path: Path) -> Iterator[OwnerResponsivenessFixture]:
    fixture = OwnerResponsivenessFixture(tmp_path, 6, mixed=True)
    try:
        _decide(fixture, fixture.cases[3], _REVISIONS, "many")
        _decide(fixture, fixture.cases[4], 1, "one")
        yield fixture
    finally:
        fixture.close()


def _rows(fixture: OwnerResponsivenessFixture, case: ReviewCase) -> list[tuple]:
    return fixture.repository._connection.execute(
        "SELECT ordinal,document FROM human_decisions WHERE review_case_id=? "
        "ORDER BY ordinal",
        (case.review_case_id,),
    ).fetchall()


def _sealed(decision: HumanDecision, **changes: object) -> HumanDecision:
    """Re-seal a decision so it parses: only the case can refuse it."""
    value = dataclasses.replace(decision, **changes)  # type: ignore[arg-type]
    request = {
        key: value.to_data()[key]
        for key in (
            "actor",
            "application",
            "destination",
            "idempotency_digest",
            "outcome",
            "proposal_id",
            "review_case_id",
            "selected_value",
            "target_fingerprint",
        )
    }
    digest = human_decision._sha256(human_decision._canonical(request))
    identity = "human_decision_" + human_decision._sha256(
        human_decision._canonical(
            {"request_digest": digest, "occurred_at": value.occurred_at}
        )
    )
    return dataclasses.replace(value, request_digest=digest, decision_id=identity)


def _replace(
    fixture: OwnerResponsivenessFixture, ordinal: int, document: str, **columns: object
) -> None:
    connection = fixture.repository._connection
    connection.commit()
    # A re-sealed decision has a new identity its recorded event no longer names.
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("DROP TRIGGER IF EXISTS human_decisions_no_update")
    assignments = ",".join(f"{name}=?" for name in ("document", *columns))
    connection.execute(
        f"UPDATE human_decisions SET {assignments} WHERE ordinal=?",
        (document, *columns.values(), ordinal),
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")


def _resealed(
    fixture: OwnerResponsivenessFixture, ordinal: int, **changes: object
) -> None:
    document = fixture.repository._connection.execute(
        "SELECT document FROM human_decisions WHERE ordinal=?", (ordinal,)
    ).fetchone()[0]
    decision = _sealed(HumanDecision.from_json(document), **changes)
    _replace(
        fixture,
        ordinal,
        decision.to_json(),
        decision_id=decision.decision_id,
        proposal_id=decision.proposal_id,
        target_fingerprint=decision.target_fingerprint,
        destination=decision.destination,
        outcome=decision.outcome,
        selected_value_document=json.dumps(decision.selected_value),
        request_digest=decision.request_digest,
    )


def _first(fixture: OwnerResponsivenessFixture) -> int:
    return int(_rows(fixture, fixture.cases[3])[0][0])


def _later(fixture: OwnerResponsivenessFixture) -> int:
    return int(_rows(fixture, fixture.cases[3])[-1][0])


def _superseded(fixture: OwnerResponsivenessFixture) -> int:
    # The middle revision: a later one supersedes it, as one supersedes the first.
    rows = _rows(fixture, fixture.cases[3])
    return int(rows[len(rows) // 2][0])


_OTHER = "f" * 64
_UNKNOWN_PROPOSAL = "review_proposal_" + "e" * 64
# Each mutation leaves the case's other decisions valid, so the refusal is the
# mutated decision's: a malformed or inconsistent first, later or superseded
# revision on a case with several destinations decided three times each.
_MUTATIONS: dict[str, Callable[[OwnerResponsivenessFixture], None]] = {
    "valid": lambda fixture: None,
    "first malformed": lambda fixture: _replace(fixture, _first(fixture), "{}"),
    "later malformed": lambda fixture: _replace(fixture, _later(fixture), "{}"),
    "later unknown proposal": lambda fixture: _resealed(
        fixture, _later(fixture), proposal_id=_UNKNOWN_PROPOSAL
    ),
    "later other target": lambda fixture: _resealed(
        fixture, _later(fixture), target_fingerprint=_OTHER
    ),
    "later other case": lambda fixture: _resealed(
        fixture, _later(fixture), review_case_id=fixture.cases[4].review_case_id
    ),
    "later recorded only": lambda fixture: _resealed(
        fixture, _later(fixture), application="recorded_only"
    ),
    "later unoffered selection": lambda fixture: _resealed(
        fixture, _later(fixture), outcome="accept", selected_value={"value": "x"}
    ),
    "later column disagrees": lambda fixture: _replace(
        fixture,
        _later(fixture),
        _rows(fixture, fixture.cases[3])[-1][1],
        occurred_at=7,
    ),
    "superseded unknown proposal": lambda fixture: _resealed(
        fixture, _superseded(fixture), proposal_id=_UNKNOWN_PROPOSAL
    ),
}


def _refusal(call: Callable[[], object]) -> list[tuple[str, str]] | None:
    """The refusal and every error it was raised from, or None."""
    try:
        call()
    except Exception as error:
        chain: list[tuple[str, str]] = []
        current: BaseException | None = error
        while current is not None:
            chain.append((type(current).__name__, str(current)))
            current = current.__cause__ or (
                None if current.__suppress_context__ else current.__context__
            )
        return chain
    return None


def _outcome(call: Callable[[], object]) -> object:
    """A result's digest, or its refusal chain."""
    results: list[object] = []
    refusal = _refusal(lambda: results.append(call()))
    if refusal is not None:
        return refusal
    serialized = json.dumps(
        dataclasses.asdict(results[0]),  # type: ignore[call-overload]
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _routes(
    fixture: OwnerResponsivenessFixture,
) -> dict[str, Callable[[], object]]:
    application = fixture.application
    case = fixture.cases[3]
    position = next(
        index
        for index, item in enumerate(
            application.library(lens="all", search=None, limit=50, cursor=None).items
        )
        if item["review_case_id"] == case.review_case_id
    )
    # A first-row case would have no read-ahead position to test.
    assert position > 0

    def library(lens: str, search: str | None = None, limit: int = 50):
        return lambda: application.library(
            lens=lens, search=search, limit=limit, cursor=None
        )

    return {
        "library": library("all"),
        "lens": library("needs_attention"),
        "search": library("all", "0003"),
        # The decided case is the read-ahead row the page fetches but does not
        # show; the store still validates its decisions.
        "read-ahead": library("all", limit=position),
        "detail": lambda: application.case_detail(case.review_case_id),
    }


def _outcomes(tmp_path: Path, mutation: str) -> dict[str, object]:
    with _decided(tmp_path) as fixture:
        routes = _routes(fixture)
        _MUTATIONS[mutation](fixture)
        return {name: _outcome(call) for name, call in routes.items()}


_UNAVAILABLE = ("ReviewWebError", "unavailable")
_LIBRARY = ("MetadataReportError", "review library is unavailable")
_DETAIL = ("MetadataReportError", "human decision is unavailable")


def _store_refusal(*cause: tuple[str, str]) -> dict[str, object]:
    """The page refusal every Library route raises, and the detail's."""
    return {
        **{
            name: [_UNAVAILABLE, _LIBRARY, *cause]
            for name in ("library", "lens", "search", "read-ahead")
        },
        "detail": [_UNAVAILABLE, _DETAIL, *cause],
    }


_STALE = ("HumanDecisionError", "stale")
_INVALID = ("HumanDecisionError", "invalid")
# Captured at base a1d73710, before the case was projected once per row, and
# verified unchanged at da64015c.
_PINNED: dict[str, object] = {
    "first malformed": _store_refusal(_INVALID, ("ValueError", "")),
    "later malformed": _store_refusal(_INVALID, ("ValueError", "")),
    "later unknown proposal": _store_refusal(_STALE),
    "later other target": _store_refusal(_STALE),
    "later other case": _store_refusal(_STALE),
    "later recorded only": _store_refusal(_STALE),
    "later unoffered selection": _store_refusal(
        ("HumanDecisionError", "invalid_selection")
    ),
    "later column disagrees": {
        **{
            name: [_UNAVAILABLE, _LIBRARY]
            for name in ("library", "lens", "search", "read-ahead")
        },
        "detail": [_UNAVAILABLE, ("MetadataReportError", "human decision is invalid")],
    },
    "superseded unknown proposal": _store_refusal(_STALE),
}


@pytest.mark.parametrize("mutation", _PINNED)
def test_a_row_refuses_each_decision_exactly_as_before(
    tmp_path: Path, mutation: str
) -> None:
    assert _outcomes(tmp_path, mutation) == _PINNED[mutation]


def test_valid_decisions_leave_every_route_readable(tmp_path: Path) -> None:
    outcomes = _outcomes(tmp_path, "valid")
    assert all(type(value) is str for value in outcomes.values()), outcomes


def _old_validate_for_browse_case(
    decision: HumanDecision, review_case_id: str, case: dict[str, object]
) -> None:
    """`HumanDecision.validate_for_browse_case` verbatim at a1d73710."""
    target_fingerprint = case.get("target_fingerprint")
    proposal_set_id = case.get("proposal_set_id")
    proposal_document = case.get("proposal_document")
    results = case.get("results")
    if (
        type(review_case_id) is not str
        or type(target_fingerprint) is not str
        or type(proposal_set_id) is not str
        or type(proposal_document) is not str
        or type(results) is not list
        or any(type(result) is not dict for result in results)
    ):
        raise HumanDecisionError("stale")
    try:
        proposals = review_proposals(proposal_set_id, proposal_document, results)
        media_kind = proposal_media_kind(proposal_document)
    except ReviewCaseValueError as error:
        raise HumanDecisionError("stale") from error
    decision._validate_for(
        review_case_id,
        target_fingerprint,
        proposals,
        applications=admitted_applications(media_kind),
    )


def _envelopes(case: ReviewCase) -> dict[str, tuple[str, dict[str, object]]]:
    envelope = cast("dict[str, object]", json.loads(case.to_json()))
    proposal = json.loads(case.proposal_document)
    return {
        "exact": (case.review_case_id, envelope),
        "id not text": (cast(str, None), envelope),
        "other id": ("review_case_" + _OTHER, envelope),
        **{
            f"{key} absent": (
                case.review_case_id,
                {k: v for k, v in envelope.items() if k != key},
            )
            for key in (
                "target_fingerprint",
                "proposal_set_id",
                "proposal_document",
                "results",
            )
        },
        "result not object": (
            case.review_case_id,
            {**envelope, "results": [*cast(list, envelope["results"])[:-1], []]},
        ),
        "results differ": (
            case.review_case_id,
            {**envelope, "results": cast(list, envelope["results"])[:-1]},
        ),
        "proposal not json": (
            case.review_case_id,
            {**envelope, "proposal_document": "{"},
        ),
        "proposal media unknown": (
            case.review_case_id,
            {
                **envelope,
                "proposal_document": json.dumps({**proposal, "media_kind": "scroll"}),
            },
        ),
        "other target": (
            case.review_case_id,
            {**envelope, "target_fingerprint": _OTHER},
        ),
    }


def _validate_in_browse_scope(
    decision: HumanDecision, review_case_id: str, case: dict[str, object]
) -> None:
    from mechanical_librarian.domain.human_decision import DecisionScope

    decision.validate_in(DecisionScope.of_browse_case(review_case_id, case))


def test_the_browse_scope_refuses_as_each_decision_did(tmp_path: Path) -> None:
    with _decided(tmp_path) as fixture:
        case = fixture.cases[3]
        decisions = [HumanDecision.from_json(row[1]) for row in _rows(fixture, case)]
    decisions += [
        _sealed(decisions[-1], proposal_id=_UNKNOWN_PROPOSAL),
        _sealed(decisions[-1], application="recorded_only"),
    ]
    compared = 0
    for name, (review_case_id, envelope) in _envelopes(case).items():
        for decision in decisions:
            expected = _refusal(
                partial(
                    _old_validate_for_browse_case, decision, review_case_id, envelope
                )
            )
            actual = _refusal(
                partial(_validate_in_browse_scope, decision, review_case_id, envelope)
            )
            assert actual == expected, (name, decision.decision_id)
            compared += expected is not None
    assert compared > len(decisions)


def test_the_store_projects_the_case_only_once_a_decision_has_parsed(
    tmp_path: Path,
) -> None:
    # A malformed first decision on a case whose proposals cannot be projected
    # is refused as malformed: the store builds its scope only after the first
    # decision parses, as each decision once projected the case after parsing.
    # Building it before the loop would refuse it as stale instead. The same
    # eager move in `get_review_case_decisions` is an equivalent mutant there:
    # `ReviewCase.__post_init__` already validates the proposals, so
    # `case.proposals` cannot fail once `ReviewCase.from_json` has returned.
    with _decided(tmp_path) as fixture:
        case = fixture.cases[3]
        _replace(fixture, _first(fixture), "{}")
        review_case_id, envelope = _envelopes(case)["proposal not json"]
        refusal = _refusal(
            lambda: fixture.repository._review_projections._browse_decision_documents(
                review_case_id, envelope
            )
        )
    assert refusal == [_LIBRARY, _INVALID, ("ValueError", "")]


@contextmanager
def _projections() -> Iterator[Counter[str]]:
    """Count case projections by the function that asked for one."""
    import sys

    counted: Counter[str] = Counter()
    # Every projection, from a parsed case or from its documents, ends here.
    original = review_case._review_proposals

    def counting(*args: object) -> tuple[dict[str, object], ...]:
        depth = 1
        while sys._getframe(depth).f_code.co_name in {
            "proposals",
            "review_proposals",
            "of",
            "of_browse_case",
        }:
            depth += 1
        counted[sys._getframe(depth).f_code.co_name] += 1
        return original(*args)  # type: ignore[arg-type]

    with patch.object(review_case, "_review_proposals", counting):
        yield counted


def test_each_row_projects_its_case_once_for_all_its_decisions(
    tmp_path: Path,
) -> None:
    with _decided(tmp_path) as fixture:
        application = fixture.application
        case = fixture.cases[3]
        assert len(_rows(fixture, case)) >= 2 * _REVISIONS
        decided = fixture.repository._connection.execute(
            "SELECT count(DISTINCT review_case_id) FROM human_decisions"
        ).fetchone()[0]
        with _projections() as library:
            application.library(lens="all", search=None, limit=50, cursor=None)
        with _projections() as detail:
            application.case_detail(case.review_case_id)
    # One projection per decided row, in the store; the row projection reuses
    # it rather than projecting again per decision.
    assert library == {"_browse_decision_documents": decided}
    # One for the decisions' scope, one for the fields the detail shows.
    assert detail == {"get_review_case_decisions": 1, "_read": 1}


def test_a_scope_for_another_case_is_set_aside_and_the_case_projected(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.application.review_web import BrowseReviewLibrary
    from mechanical_librarian.domain.human_decision import DecisionScope

    with _decided(tmp_path) as fixture:
        repository = fixture.repository
        snapshot = repository.read_review_library(None, 50, 0, None)
        scopes = snapshot.decision_scopes
        decided = [index for index, scope in enumerate(scopes) if scope is not None]
        assert len(decided) > 1
        # A store that hands over no scope, and one that hands over the wrong
        # case's scope: each decided row then projects its own canonical case.
        other = cast(DecisionScope, scopes[decided[1]])
        variants = {
            "shared": snapshot,
            "absent": dataclasses.replace(snapshot, decision_scopes=()),
            "foreign": dataclasses.replace(
                snapshot,
                decision_scopes=tuple(
                    other if scope is not None else None for scope in scopes
                ),
            ),
        }
        pages: dict[str, object] = {}
        counts: dict[str, Counter[str]] = {}
        for name, variant in variants.items():
            with (
                patch.object(repository, "read_review_library", return_value=variant),
                _projections() as counted,
            ):
                pages[name] = BrowseReviewLibrary(repository).library(
                    lens="all", search=None, limit=50, cursor=None
                )
            counts[name] = counted
    assert pages["absent"] == pages["shared"] == pages["foreign"]
    assert counts == {
        "shared": {},
        "absent": {"_library_item": len(decided)},
        # The row whose scope is its own reuses it.
        "foreign": {"_library_item": len(decided) - 1},
    }


def test_the_row_projection_still_refuses_a_decision_its_store_let_through(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.application.review_web import BrowseReviewLibrary

    with _decided(tmp_path) as fixture:
        repository = fixture.repository
        snapshot = repository.read_review_library(None, 50, 0, None)
        index, row = next(
            (index, row)
            for index, row in enumerate(snapshot.rows)
            if snapshot.decision_scopes[index] is not None
        )
        stale = _sealed(
            HumanDecision.from_json(row[2][-1]), proposal_id=_UNKNOWN_PROPOSAL
        ).to_json()
        rows = list(snapshot.rows)
        rows[index] = (row[0], row[1], (*row[2], stale), *row[3:])
        tampered = dataclasses.replace(snapshot, rows=tuple(rows))
        refusals = {}
        for name, variant in {
            "shared": tampered,
            "absent": dataclasses.replace(tampered, decision_scopes=()),
        }.items():
            with patch.object(repository, "read_review_library", return_value=variant):
                refusals[name] = _refusal(
                    lambda: BrowseReviewLibrary(repository).library(
                        lens="all", search=None, limit=50, cursor=None
                    )
                )
    # The application validates every decision it renders in the scope, whether
    # it reused the store's projection or made its own.
    assert refusals == {
        "shared": [_UNAVAILABLE, _STALE],
        "absent": [_UNAVAILABLE, _STALE],
    }
