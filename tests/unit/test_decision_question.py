"""`decision_question`: the question an owner answer was given against (ADR-0108).

The scenario tests evaluate the same report before and after the owner's own
write through the real `evaluate_report`, with only the answered target field
replaced, and compare the offer parts (C4' dry-proof Q0).
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import mechanical_librarian
from mechanical_librarian.domain.decision_question import (
    decision_question,
    offer_members,
    target_part,
    value_target_part,
)
from mechanical_librarian.domain.human_decision import offer_digest
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.genre_reports import genre_report
from tests.support.proposal_evidence import (
    _fields,
    _report,
    consensus_local_title,
    consensus_provider_title,
)


def _case(report: MetadataReport) -> ReviewCase:
    report = MetadataReport.from_json(report.to_json())
    return ReviewCase.create(evaluate_report(report), report)


def _target_field(case: ReviewCase, destination: str) -> dict[str, object] | None:
    return next(
        (
            field
            for field in case.target["fields"]
            if field.get("destination") == destination
        ),
        None,
    )


def _question(case: ReviewCase, destination: str):
    question = decision_question(
        case.proposals, _target_field(case, destination), destination
    )
    assert question is not None
    return question


def _title_report(
    target: str, local: str | None, providers: tuple[str, ...]
) -> MetadataReport:
    title = TargetField(TargetDestination.TITLE, TargetFieldState.VALUE, target)
    return _report(
        title=target,
        fields=_fields(target, title=title),
        local_claims=(consensus_local_title(local),) if local else (),
        providers=tuple(
            consensus_provider_title(provider, value)
            for provider, value in zip(
                ("open_library", "hardcover"), providers, strict=False
            )
        ),
    )


def _retarget(
    report: MetadataReport, destination: TargetDestination, value: Any
) -> MetadataReport:
    fields = tuple(
        TargetField(destination, TargetFieldState.VALUE, value)
        if field.destination is destination
        else field
        for field in report.target.fields
    )
    target = report.target
    return MetadataReport.create(
        target=TargetFacts(target.connection, target.library, target.item, fields),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        cover_sources=report.cover_sources,
    )


_DECORATED = "Plain Work [ISBN 9780306406157]"
# (before, after, destination): after is the same report with the answered
# field holding the value the owner's write put there.
_SCENARIOS: dict[str, Callable[[], tuple[MetadataReport, MetadataReport, str]]] = {
    # Two providers agree on V over the EPUB's O; the owner accepts V.
    "a1": lambda: (
        _title_report("Current Title", "Current Title", ("Plain Work", "Plain Work")),
        _title_report("Plain Work", "Current Title", ("Plain Work", "Plain Work")),
        "title",
    ),
    # Providers offer V and W beside the EPUB's O; the owner accepts V.
    "a2": lambda: (
        _title_report("Current Title", "Current Title", ("Plain Work", "Other Work")),
        _title_report("Plain Work", "Current Title", ("Plain Work", "Other Work")),
        "title",
    ),
    # The owner enters E over the a2 offer.
    "b": lambda: (
        _title_report("Current Title", "Current Title", ("Plain Work", "Other Work")),
        _title_report("Entered Work", "Current Title", ("Plain Work", "Other Work")),
        "title",
    ),
    # An evidence-led title change; the owner accepts the ISBN title.
    "e1": lambda: (
        isbn_title_report(),
        _retarget(isbn_title_report(), TargetDestination.TITLE, "Example Title"),
        "title",
    ),
    # Genres: the owner enters another genre over the provider's.
    "g2": lambda: (
        genre_report(current=("Mystery",), genres=("Fantasy",)),
        _retarget(
            genre_report(current=("Mystery",), genres=("Fantasy",)),
            TargetDestination.GENRES,
            ("Horror",),
        ),
        "genres",
    ),
}


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS), ids=sorted(_SCENARIOS))
def test_offer_part_is_kept_after_the_owners_own_write(scenario: str) -> None:
    before_report, after_report, destination = _SCENARIOS[scenario]()
    before, after = _case(before_report), _case(after_report)

    asked, written = _question(before, destination), _question(after, destination)

    assert asked.offer_part == written.offer_part
    # The write is what changed: the target half moves with it.
    assert asked.target_part != written.target_part


def test_consensus_decoration_tidy_is_asked_again_after_the_write() -> None:
    """d': the provider consensus election runs only while the target equals
    the EPUB's decorated title, so the written V offers the decorated title
    again; an expected non-carry (contract T11, questions R3)."""
    before = _case(_title_report(_DECORATED, _DECORATED, ("Plain Work", "Plain Work")))
    after = _case(_title_report("Plain Work", _DECORATED, ("Plain Work", "Plain Work")))

    assert _question(before, "title").offer_part != _question(after, "title").offer_part


def test_offer_part_names_no_proposal_id() -> None:
    case = _case(
        _title_report("Current Title", "Current Title", ("Plain Work", "Other Work"))
    )
    renamed = copy.deepcopy(case.proposals)
    for index, proposal in enumerate(renamed):
        proposal["proposal_id"] = "review_proposal_" + f"{index:064x}"

    assert offer_digest(renamed, "title") != offer_digest(case.proposals, "title")
    assert _question(case, "title") == decision_question(
        renamed, _target_field(case, "title"), "title"
    )
    assert all(
        "proposal_id" in member
        for member in offer_members(case.proposals, "title") or ()
    )


def test_title_subtitle_and_series_are_one_offer_part() -> None:
    case = _case(
        _title_report("Current Title", "Current Title", ("Plain Work", "Other Work"))
    )
    members = {
        member["destination"] for member in offer_members(case.proposals, "title") or ()
    }
    assert members == {"title", "subtitle", "series"} & {
        proposal["destination"] for proposal in case.proposals
    }
    title, series = _question(case, "title"), _question(case, "series")

    assert title.offer_part == series.offer_part
    # Each answered destination keeps its own target half.
    assert title.target_part != series.target_part
    # A series-only change asks the title answer again.
    changed = copy.deepcopy(case.proposals)
    group = next(
        proposal for proposal in changed if proposal["destination"] == "series"
    )
    group["result"]["alternatives"] = [
        *group["result"]["alternatives"],
        {
            "alternative_id": "series_offered_later",
            "value": {"kind": "series", "value": [{"name": "Other", "sequence": "1"}]},
        },
    ]
    asked = decision_question(changed, _target_field(case, "title"), "title")
    assert asked is not None
    assert asked.offer_part != title.offer_part


def test_field_the_offer_does_not_have_has_no_question() -> None:
    case = _case(_title_report("Current Title", "Current Title", ("Plain Work",)))
    offer = tuple(
        proposal
        for proposal in case.proposals
        if proposal["destination"] != "publisher"
    )

    assert (
        decision_question(offer, _target_field(case, "publisher"), "publisher") is None
    )


@pytest.mark.parametrize(
    "state", ["missing", "null", "empty"], ids=["missing", "null", "empty"]
)
def test_empty_target_states_fold_into_one(state: str) -> None:
    assert target_part("subtitle", {"state": state, "value": None}) == target_part(
        "subtitle", {"state": "missing", "value": None}
    )
    assert target_part("subtitle", {"state": state}) == value_target_part(
        "subtitle", (None,)
    )


@pytest.mark.parametrize(
    "state",
    ["unloaded", "malformed", "oversized"],
    ids=["unloaded", "malformed", "oversized"],
)
def test_unreadable_target_states_stay_distinct(state: str) -> None:
    unreadable = target_part("subtitle", {"state": state, "value": None})

    assert unreadable != target_part("subtitle", {"state": "empty", "value": None})
    assert unreadable != target_part("subtitle", None)
    assert unreadable != target_part("subtitle", {"state": "value", "value": "A"})
    assert (
        len(
            {
                target_part("subtitle", {"state": other})
                for other in ("unloaded", "malformed", "oversized")
            }
        )
        == 3
    )


def test_a_field_the_case_did_not_read_is_its_own_target_state() -> None:
    assert target_part("subtitle", None) != target_part(
        "subtitle", {"state": "empty", "value": None}
    )


def test_written_value_reads_as_the_case_that_shows_it() -> None:
    case = _case(_title_report("Plain Work", "Current Title", ("Plain Work",)))

    assert (
        value_target_part("title", ("Plain Work",))
        == _question(case, "title").target_part
    )
    # Only the state and value count, not `restorable` or other metadata.
    assert target_part(
        "title", {"state": "value", "value": "Plain Work", "restorable": True}
    ) == value_target_part("title", ("Plain Work",))


def test_no_owner_plan_writes_a_subtitle_companion() -> None:
    """The target part names the answered destination only. ADR-0108 adds
    the subtitle where an owner answer's plan writes it as a companion; no
    owner plan does today (contract T1, Q7): the one companion is built in
    `plan_title_subtitle`, which takes two automatic decisions. A new
    companion site must add the subtitle to the target part first."""
    source = Path(mechanical_librarian.__file__).parent
    sites: list[tuple[str, str]] = []
    for path in sorted(source.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "SubtitleCompanion"
                ):
                    annotations = {
                        argument.arg: ast.unparse(argument.annotation)
                        for argument in function.args.args
                        if argument.annotation is not None
                    }
                    assert annotations.get("title") == "AutomaticDecision"
                    assert annotations.get("subtitle") == "AutomaticDecision"
                    sites.append((path.name, function.name))

    assert sites == [("field_effects.py", "plan_title_subtitle")]
