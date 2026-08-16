"""Synthetic installed-surface lifecycle proof for review admission storage."""
import json
import sys
import tempfile
import threading
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report


def _actionable(report):
    return evaluate_report(report)


def _report(
    title: str = "Title",
    content_digest: str = "a",
    file_identity: str = "b",
    *,
    actionable: bool = True,
) -> MetadataReport:
    claims: tuple[LocalClaim, ...] = ()
    reasons = ("no_opening_claims",)
    if actionable:
        claims = (
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Exact evaluator candidate",
                canonical="Exact evaluator candidate",
                excerpt="Exact evaluator candidate",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        )
        reasons = ("opening_matter_title",)
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(title),
        ),
        file=FileFacts(0, content_digest * 64, file_identity * 64),
        local_claims=claims,
        local_reasons=reasons,
        providers=(),
    )


def test_absent_restart_replay_successor_and_retirement():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reviews.sqlite"
        repository = open_repository(database)
        first = _report()
        repository.publish(first, lambda: True)
        proposal = _actionable(first)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        case_id = admitted["review_case_id"]
        assert "admitted" == admitted["status"]
        assert "replayed" == json.loads(repository.admit_review_case(proposal))["status"]
        repository.close()
        repository = open_repository(database, create=False)
        assert json.loads(repository.get_review_case(case_id))["current"]
        successor = _report("Changed", "c", "d")
        repository.publish(successor, lambda: True)
        later = _actionable(successor)
        repository.publish_proposal(later)
        later_case = json.loads(repository.admit_review_case(later))["review_case_id"]
        assert not (json.loads(repository.get_review_case(case_id))["current"])
        assert json.loads(repository.get_review_case(later_case))["current"]
        retired_report = _report("Retired", "e", "f", actionable=False)
        repository.publish(retired_report, lambda: True)
        retired = evaluate_report(retired_report)
        repository.publish_proposal(retired)
        assert "retired" == json.loads(repository.admit_review_case(retired))["status"]
        assert not (json.loads(repository.get_review_case(later_case))["current"])
        assert (2,) == repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone()
        repository.close()


def test_concurrent_identical_admission_converges():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reviews.sqlite"
        repository = open_repository(database)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        repository.close()
        barrier, outcomes = threading.Barrier(2), []
        def admit():
            if sys.platform.startswith("linux"):
                barrier.wait()
                local = open_repository(database, create=False)
            else:
                local = open_repository(database, create=False)
                barrier.wait()
            try:
                outcomes.append(json.loads(local.admit_review_case(proposal)))
            finally:
                local.close()

        workers = [threading.Thread(target=admit) for _ in range(2)]
        # The comprehensions are used for their effect; the collected `None`
        # results are discarded.
        [worker.start() for worker in workers]  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        [worker.join() for worker in workers]  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        assert 1 == len({value["review_case_id"] for value in outcomes})
