import unittest

from mechanical_librarian.application.proposal_sets import (
    CreateProposalSet,
    GetProposalSet,
    ProposalSetError,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report


def _report() -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", "Title"),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )


class _Repository:
    def __init__(self, report: MetadataReport) -> None:
        self.report = report
        self.proposals: dict[str, str] = {}
        self.events: list[str] = []

    def get(self, report_id: str) -> str:
        self.events.append("get_report")
        if report_id != self.report.report_id:
            raise ValueError
        return self.report.to_json()

    def publish_proposal(self, proposal_set) -> str:
        self.events.append("publish")
        serialized = proposal_set.to_json()
        self.proposals[proposal_set.proposal_set_id] = serialized
        return serialized

    def get_proposal(self, proposal_set_id: str) -> str:
        self.events.append("get_proposal")
        return self.proposals[proposal_set_id]

    def close(self) -> None:
        pass


class ProposalApplicationTests(unittest.TestCase):
    def test_currentness_precedes_publication_and_readback_is_exact(self) -> None:
        report = _report()
        repository = _Repository(report)

        def current(value: MetadataReport) -> bool:
            self.assertEqual(report, value)
            repository.events.append("current")
            return True

        serialized = CreateProposalSet(repository, current).run(
            report.report_id
        )
        proposal_set = evaluate_report(report)
        self.assertEqual(proposal_set.to_json(), serialized)
        self.assertEqual(
            ["get_report", "current", "publish", "get_proposal"],
            repository.events,
        )
        self.assertEqual(
            serialized,
            GetProposalSet(repository).run(proposal_set.proposal_set_id),
        )

    def test_stale_report_publishes_nothing(self) -> None:
        report = _report()
        repository = _Repository(report)
        with self.assertRaises(ProposalSetError):
            CreateProposalSet(repository, lambda _: False).run(report.report_id)
        self.assertNotIn("publish", repository.events)
        self.assertEqual({}, repository.proposals)


if __name__ == "__main__":
    unittest.main()
