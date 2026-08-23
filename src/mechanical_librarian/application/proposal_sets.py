"""Create and reopen immutable ProposalSets from exact current reports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.metadata_report import (
    ProposalNotFoundError,
    ReportNotFoundError,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)


class ProposalSetError(RuntimeError):
    """A ProposalSet operation failed without exposing rejected values."""


class ProposalCurrentnessError(ProposalSetError):
    """The exact report target changed before proposal publication."""


class ProposalMissingInputError(ProposalSetError):
    """A required report or proposal document is absent."""


class ProposalRetainedDataError(ProposalSetError):
    """Retained report or proposal data cannot be safely reopened."""


class ProposalPolicyError(ProposalSetError):
    """Policy resolution did not produce a usable proposal policy."""


class ProposalEvaluationError(ProposalSetError):
    """Proposal evaluation did not complete."""


class ProposalPublicationError(ProposalSetError):
    """Proposal publication did not complete."""


class ProposalInternalError(ProposalSetError):
    """An internal proposal operation did not complete."""


class ProposalSetRepository(Protocol):
    def get_proposal_input(self, report_id: str) -> str: ...

    def publish_proposal(self, proposal_set: ProposalSet) -> str: ...

    def get_proposal(self, proposal_set_id: str) -> str: ...

    def close(self) -> None: ...


class CreateProposalSet:
    """Validate one exact report, evaluate fixed policy, and publish atomically."""

    def __init__(
        self,
        repository: ProposalSetRepository,
        require_current: Callable[[MetadataReport], bool],
        resolve_policy_profile: Callable[[MetadataReport], ResolvedMetadataPolicy | str]
        | None = None,
    ) -> None:
        self._repository = repository
        self._require_current = require_current
        self._resolve_policy_profile = resolve_policy_profile or (
            lambda report: (
                resolve_policy(
                    MetadataPolicyProfile.builtin(), report.target.library
                ).resolved_id
            )
        )

    def run(self, report_id: str) -> str:
        report = self._reopen_report(report_id)
        policy = self._resolve_policy(report)
        proposal_set = self._evaluate(report, policy)
        self._require_report_current(report)
        return self._publish(proposal_set)

    def _reopen_report(self, report_id: str) -> MetadataReport:
        try:
            serialized = self._repository.get_proposal_input(report_id)
        except ReportNotFoundError as error:
            raise ProposalMissingInputError("proposal report is unavailable") from error
        except ReportValueError as error:
            raise ProposalRetainedDataError("proposal report is malformed") from error
        except Exception as error:
            raise ProposalInternalError(
                "proposal report could not be reopened"
            ) from error
        try:
            report = MetadataReport.from_json(serialized)
        except ReportValueError as error:
            raise ProposalRetainedDataError("proposal report is malformed") from error
        if report.report_id != report_id:
            raise ProposalRetainedDataError("proposal report identity disagrees")
        return report

    def _resolve_policy(self, report: MetadataReport) -> ResolvedMetadataPolicy | str:
        try:
            return self._resolve_policy_profile(report)
        except (ProposalSetError, TargetConnectionError):
            raise
        except Exception as error:
            raise ProposalPolicyError("proposal policy resolution failed") from error

    def _evaluate(
        self, report: MetadataReport, policy: ResolvedMetadataPolicy | str
    ) -> ProposalSet:
        # ``type(policy) is str`` is an exact-type test over the declared
        # ``ResolvedMetadataPolicy | str`` union, so each arm is already
        # proven; mypy simply does not narrow inside a conditional expression.
        try:
            return evaluate_report(
                report,
                policy
                if type(policy) is str
                else cast("ResolvedMetadataPolicy", policy).resolved_id,
                None
                if type(policy) is str
                else human_review_destinations(cast("ResolvedMetadataPolicy", policy)),
            )
        except ProposalSetError:
            raise
        except Exception as error:
            raise ProposalEvaluationError("proposal evaluation failed") from error

    def _require_report_current(self, report: MetadataReport) -> None:
        try:
            current = self._require_current(report)
        except (ProposalSetError, TargetConnectionError):
            raise
        except Exception as error:
            raise ProposalInternalError("proposal currentness check failed") from error
        if current is not True:
            raise ProposalCurrentnessError("proposal report is stale")

    def _publish(self, proposal_set: ProposalSet) -> str:
        try:
            persisted = self._repository.publish_proposal(proposal_set)
        except Exception as error:
            raise ProposalPublicationError("proposal publication failed") from error
        try:
            reopened = ProposalSet.from_json(persisted)
            readback = self._repository.get_proposal(proposal_set.proposal_set_id)
        except ProposalValueError as error:
            raise ProposalRetainedDataError(
                "proposal publication is malformed"
            ) from error
        except Exception as error:
            raise ProposalPublicationError(
                "proposal publication readback failed"
            ) from error
        if (
            persisted != proposal_set.to_json()
            or reopened.proposal_set_id != proposal_set.proposal_set_id
            or readback != persisted
        ):
            raise ProposalRetainedDataError("proposal publication readback disagrees")
        return persisted


class GetProposalSet:
    """Reopen one exact canonical ProposalSet without operational configuration."""

    def __init__(self, repository: ProposalSetRepository) -> None:
        self._repository = repository

    def run(self, proposal_set_id: str) -> str:
        try:
            serialized = self._repository.get_proposal(proposal_set_id)
        except (KeyError, ProposalNotFoundError) as error:
            raise ProposalMissingInputError("proposal is unavailable") from error
        except Exception as error:
            raise ProposalInternalError("proposal could not be reopened") from error
        try:
            proposal_set = ProposalSet.from_json(serialized)
        except ProposalValueError as error:
            raise ProposalRetainedDataError("proposal is malformed") from error
        if proposal_set.proposal_set_id != proposal_set_id:
            raise ProposalRetainedDataError("proposal identity disagrees")
        return serialized


__all__ = [
    "CreateProposalSet",
    "GetProposalSet",
    "ProposalCurrentnessError",
    "ProposalEvaluationError",
    "ProposalInternalError",
    "ProposalMissingInputError",
    "ProposalPolicyError",
    "ProposalPublicationError",
    "ProposalRetainedDataError",
    "ProposalSetError",
    "ProposalSetRepository",
]
