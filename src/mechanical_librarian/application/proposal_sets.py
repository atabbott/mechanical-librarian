"""Create and reopen immutable ProposalSets from exact current reports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile, resolve_policy


class ProposalSetError(RuntimeError):
    """A ProposalSet operation failed without exposing rejected values."""


class ProposalCurrentnessError(ProposalSetError):
    """The exact report target changed before proposal publication."""


class ProposalSetRepository(Protocol):
    def get(self, report_id: str) -> str: ...

    def publish_proposal(self, proposal_set: ProposalSet) -> str: ...

    def get_proposal(self, proposal_set_id: str) -> str: ...

    def close(self) -> None: ...


class CreateProposalSet:
    """Validate one exact report, evaluate fixed policy, and publish atomically."""

    def __init__(
        self,
        repository: ProposalSetRepository,
        require_current: Callable[[MetadataReport], bool],
        resolve_policy_id: Callable[[MetadataReport], str] | None = None,
    ) -> None:
        self._repository = repository
        self._require_current = require_current
        self._resolve_policy_id = resolve_policy_id or (
            lambda report: resolve_policy(
                MetadataPolicyProfile.builtin(), report.target.library
            ).resolved_id
        )

    def run(self, report_id: str) -> str:
        try:
            report = MetadataReport.from_json(self._repository.get(report_id))
            if report.report_id != report_id:
                raise ProposalSetError("proposal report identity disagrees")
            proposal_set = evaluate_report(report, self._resolve_policy_id(report))
            if self._require_current(report) is not True:
                raise ProposalSetError("proposal report is stale")
            persisted = self._repository.publish_proposal(proposal_set)
            if (
                persisted != proposal_set.to_json()
                or self._repository.get_proposal(proposal_set.proposal_set_id)
                != persisted
            ):
                raise ProposalSetError("proposal publication readback failed")
            return persisted
        except ProposalSetError:
            raise
        except (ReportValueError, ProposalValueError) as error:
            raise ProposalSetError("proposal input is invalid") from error
        except Exception as error:
            raise ProposalSetError("proposal publication failed") from error


class GetProposalSet:
    """Reopen one exact canonical ProposalSet without operational configuration."""

    def __init__(self, repository: ProposalSetRepository) -> None:
        self._repository = repository

    def run(self, proposal_set_id: str) -> str:
        try:
            serialized = self._repository.get_proposal(proposal_set_id)
            proposal_set = ProposalSet.from_json(serialized)
            if proposal_set.proposal_set_id != proposal_set_id:
                raise ProposalSetError("proposal identity disagrees")
            return serialized
        except ProposalSetError:
            raise
        except Exception as error:
            raise ProposalSetError("proposal is unavailable") from error


__all__ = [
    "CreateProposalSet",
    "GetProposalSet",
    "ProposalCurrentnessError",
    "ProposalSetError",
    "ProposalSetRepository",
]
