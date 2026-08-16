"""Create and reopen immutable ProposalSets from exact current reports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from mechanical_librarian.application.catalogue import TargetConnectionError
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
        resolve_policy_profile: Callable[[MetadataReport], ResolvedMetadataPolicy | str] | None = None,
    ) -> None:
        self._repository = repository
        self._require_current = require_current
        self._resolve_policy_profile = resolve_policy_profile or (
            lambda report: resolve_policy(
                MetadataPolicyProfile.builtin(), report.target.library
            ).resolved_id
        )

    def run(self, report_id: str) -> str:
        try:
            report = MetadataReport.from_json(self._repository.get(report_id))
            if report.report_id != report_id:
                raise ProposalSetError("proposal report identity disagrees")
            policy = self._resolve_policy_profile(report)
            # ``type(policy) is str`` is an exact-type test over the declared
            # ``ResolvedMetadataPolicy | str`` union, so each arm is already
            # proven; mypy simply does not narrow inside a conditional
            # expression.
            proposal_set = evaluate_report(
                report,
                policy
                if type(policy) is str
                else cast("ResolvedMetadataPolicy", policy).resolved_id,
                None
                if type(policy) is str
                else human_review_destinations(
                    cast("ResolvedMetadataPolicy", policy)
                ),
            )
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
        except TargetConnectionError:
            raise
        except (ReportValueError, ProposalValueError) as error:
            raise ProposalSetError("proposal input is invalid") from error
        except Exception as error:
            # This spans the whole run, not the publication alone: policy
            # resolution, report evaluation, and the currency check are inside
            # the same block, and each reaches an injected seam that declares no
            # error type. So the message names the outcome the caller gets --
            # no published proposal set -- and not the phase that failed; a
            # policy resolver that raises is reported here too, before any
            # publication was attempted. What the message does rule out is the
            # one thing above it: the domain parsers name their malformed-input
            # failures, those are matched already, and a fault that reached here
            # is not a valid-input problem and must not be reported as one.
            # Separating the phases would move caller-visible error identities
            # that no proven misclassification justifies moving.
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
            # One untyped repository read feeding one domain parser: a stored
            # document that will not reopen and a store that would not produce
            # it are the same outcome to a caller, and neither is separable
            # from the other without an error type the seam does not declare.
            raise ProposalSetError("proposal is unavailable") from error


__all__ = [
    "CreateProposalSet",
    "GetProposalSet",
    "ProposalCurrentnessError",
    "ProposalSetError",
    "ProposalSetRepository",
]
