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
from mechanical_librarian.domain.level_proposal_set import derive_level
from mechanical_librarian.domain.metadata_report import canonical_json
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase, review_proposals
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.support.genre_reports import genre_report
from tests.support.ledger_partition import rewrite
from tests.unit.test_level_proposal_sets import _policy as _level_policy
from tests.unit.test_level_proposal_sets import _refused, _synopsis
from tests.unit.test_level_proposal_sets import _report as _level_report

_REPORT = genre_report(current=("Drama",), genres=("Fantasy",))
# What 881a92c's evaluator, before the epoch existed, made of `_REPORT`,
# restated under fixed-conservative-v49 and metadata-report-v84 (#1880).
_PROPOSAL_SET_ID = (
    "proposal_set_bcfff5d2b96372b4b476321cc16e85f5fe110b06cce471f44e38951316d3d86c"
)
_REVIEW_CASE_ID = (
    "review_case_0fd61c31ac3b7803b133bbb96146ea57df411e1411b9cd8b817489fe1dd018e0"
)
_FIRST_REVIEW_PROPOSAL_ID = (
    "review_proposal_0fc4e855beba609be743346094d0b8c7f3b5e8b87a79de21339b0dfa48c141f9"
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


def test_a_level_derivation_and_a_shadow_check_keep_the_sources_epoch() -> None:
    """The level set is evaluated at its live set's epoch, and a shadow packet
    is checked against its supplied set at that set's epoch."""
    report = _level_report(_refused(_synopsis()))
    policy = _level_policy(report)
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    derived: dict[int, str] = {}
    for epoch in (0, 1):
        live = evaluate_report(report, policy.resolved_id, derived_epoch=epoch)
        derivation = derive_level(
            report, live, ReviewCase.create(live, report), policy, 1, level
        )
        derived[epoch] = derivation.level_proposal_set_id
        level_set = evaluate_report(
            report,
            live.resolved_policy_id,
            work_identity_level=level,
            derived_epoch=epoch,
        )
        stored = json.loads(derivation.document)["level_set"]
        assert stored == json.loads(level_set.to_json())
        assert stored.get("derived_epoch", 0) == epoch
        packet = ShadowReviewPacket.create(
            ReviewCase.create(level_set, report),
            level_set,
            report,
            include_policy_ignored=True,
            work_identity_level=level,
        )
        packet.validate_source(report, level_set, None, work_identity_level=level)
    assert derived[0] != derived[1]
    # Re-evaluating the epoch 1 set at epoch 0 disagrees, which is what the
    # check would compare had it not taken the supplied set's epoch.
    assert (
        evaluate_report(report, policy.resolved_id, work_identity_level=level).to_json()
        != level_set.to_json()
    )


def test_the_unchanged_proposal_check_reads_the_ledgers_epoch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.sqlite3"
    open_repository(path).close()
    rewrite(path, "UPDATE ledger_meta SET derived_epoch=1")
    repository = open_repository(path, create=False)
    try:
        repository.publish(_REPORT, lambda: True, None)
        published = ProposalSet.from_json(
            CreateProposalSet(repository, lambda _: True).run(_REPORT.report_id)
        )
        same = repository._review_preparation._proposes_the_same
        assert same(_REPORT, _REPORT.report_id, published.proposal_set_id)
        # The epoch 0 set of the same report is not what this epoch proposes.
        assert not same(_REPORT, _REPORT.report_id, _PROPOSAL_SET_ID)
    finally:
        repository.close()
