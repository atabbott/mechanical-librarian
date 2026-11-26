"""The derived epoch (ADR-0108, #2040).

Every derived id a regeneration rebuilds is addressed over the ledger's
`derived_epoch`, so a case of a later epoch never shares an id with one of an
earlier epoch. At epoch 0 the proposal document omits the key, so nothing a
store of this build already holds changes id.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.proposal_sets import CreateProposalSet
from mechanical_librarian.domain.metadata_report import canonical_json
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase, review_proposals
from tests.support.genre_reports import genre_report
from tests.support.ledger_partition import rewrite

_REPORT = genre_report(current=("Drama",), genres=("Fantasy",))
# What 881a92c's evaluator, before the epoch existed, made of `_REPORT`.
_PROPOSAL_SET_ID = (
    "proposal_set_d9cbd8c0ca0fa34d86b1bd049ec333c0c1fbb8a3f519bdbd5119f542fe9a3fb3"
)
_REVIEW_CASE_ID = (
    "review_case_90e22892ba84dd7a5f8a606635a07349a662b1d6fec1ad1356b6debf55339dec"
)
_FIRST_REVIEW_PROPOSAL_ID = (
    "review_proposal_802ba7bdfb21db6dff23debc22942f6503dc4e42e31bfae4e9e0990aadf6ade6"
)
_SRC = Path(__file__).parents[2] / "src" / "mechanical_librarian"
# The calls that need no epoch: a title conflict reads only the results, which
# do not depend on it, and a shadow packet checked with no supplied proposal
# has no src caller (#2040 S3).
_WITHOUT_EPOCH = Counter(
    {
        ("application/cover_policy.py", "_title_conflict"): 1,
        ("domain/shadow_review.py", "validate_source"): 1,
    }
)


def _ids(proposal: ProposalSet) -> tuple[str, str, str]:
    case = ReviewCase.create(proposal, _REPORT)
    first = review_proposals(
        proposal.proposal_set_id, proposal.to_json(), case.results
    )[0]["proposal_id"]
    return proposal.proposal_set_id, case.review_case_id, str(first)


def test_epoch_0_changes_no_document_or_id() -> None:
    proposal = evaluate_report(_REPORT, derived_epoch=0)
    assert proposal == evaluate_report(_REPORT)
    assert proposal.derived_epoch == 0
    assert "derived_epoch" not in json.loads(proposal.to_json())
    assert _ids(proposal) == (
        _PROPOSAL_SET_ID,
        _REVIEW_CASE_ID,
        _FIRST_REVIEW_PROPOSAL_ID,
    )


def test_epoch_1_changes_the_proposal_set_review_proposal_and_case_ids() -> None:
    zero = evaluate_report(_REPORT)
    one = evaluate_report(_REPORT, derived_epoch=1)
    assert json.loads(one.to_json())["derived_epoch"] == 1
    assert one.results == zero.results
    assert ProposalSet.from_json(one.to_json()) == one
    assert all(
        later != earlier for later, earlier in zip(_ids(one), _ids(zero), strict=True)
    )
    assert (
        ReviewCase.create(one, _REPORT).results
        == ReviewCase.create(zero, _REPORT).results
    )


@pytest.mark.parametrize(
    "value",
    [0, True, -1, "1", 1.0],
    ids=["zero", "bool", "negative", "string", "float"],
)
def test_a_document_naming_epoch_0_or_a_non_integer_is_refused(value: object) -> None:
    data = json.loads(evaluate_report(_REPORT, derived_epoch=1).to_json())
    data["derived_epoch"] = value
    with pytest.raises(ProposalValueError):
        ProposalSet.from_json(canonical_json(data))


@pytest.mark.parametrize(
    "value", [True, -1, "1", 1.0], ids=["bool", "negative", "string", "float"]
)
def test_evaluation_refuses_an_epoch_that_is_not_a_whole_number(value: object) -> None:
    with pytest.raises(ProposalValueError):
        evaluate_report(_REPORT, derived_epoch=value)  # type: ignore[arg-type]  # reason: the refusal of a wrongly typed epoch is the contract under test.


def _calls_without_epoch() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func
                evaluates = (
                    isinstance(callee, ast.Name) and callee.id == "evaluate_report"
                ) or (
                    isinstance(callee, ast.Attribute)
                    and callee.attr == "create"
                    and isinstance(callee.value, ast.Name)
                    and callee.value.id == "ProposalSet"
                )
                if evaluates and not any(
                    keyword.arg == "derived_epoch" for keyword in node.keywords
                ):
                    found[(path.relative_to(_SRC).as_posix(), function.name)] += 1
    return found


def test_every_evaluation_in_src_names_its_epoch() -> None:
    """A call that forgot the epoch would mint an epoch 0 id after a
    regeneration, so every call names it, except the allow-listed ones."""
    assert _calls_without_epoch() == _WITHOUT_EPOCH


def test_a_store_at_a_later_epoch_publishes_and_admits_at_that_epoch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.sqlite3"
    open_repository(path).close()
    # A regeneration's epoch, set behind the store's guards (#2040 S9).
    assert rewrite(path, "UPDATE ledger_meta SET derived_epoch=1") == 1
    repository = open_repository(path, create=False)
    try:
        repository.publish(_REPORT, lambda: True, None)
        published = ProposalSet.from_json(
            CreateProposalSet(repository, lambda _: True).run(_REPORT.report_id)
        )
        assert published.derived_epoch == 1
        assert published.proposal_set_id != _PROPOSAL_SET_ID
        admitted = json.loads(repository.admit_review_case(published))
        assert admitted["status"] == "admitted"
        assert admitted["review_case_id"] != _REVIEW_CASE_ID
        read = json.loads(repository.get_review_case(admitted["review_case_id"]))
        assert read["case"]["proposal_set_id"] == published.proposal_set_id
        assert read["current"] is True
        # A proposal of another epoch is refused, so no earlier-epoch id is
        # minted beside the ledger's epoch.
        with pytest.raises(MetadataReportError, match="proposal report or policy"):
            repository.publish_proposal(evaluate_report(_REPORT))
    finally:
        repository.close()
    # Negative control: the case is re-verified at the epoch the ledger
    # names, so with the ledger set back to 0 its epoch 1 source disagrees.
    assert rewrite(path, "UPDATE ledger_meta SET derived_epoch=0") == 1
    repository = open_repository(path, create=False)
    try:
        with pytest.raises(MetadataReportError, match="source disagrees"):
            repository.get_review_case(admitted["review_case_id"])
    finally:
        repository.close()
