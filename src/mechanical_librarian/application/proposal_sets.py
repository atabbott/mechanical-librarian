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
        try:
            report = MetadataReport.from_json(self._repository.get(report_id))
            if report.report_id != report_id:
                raise ProposalSetError("proposal report identity disagrees")
            try:
                policy = self._resolve_policy_profile(report)
            except (ProposalSetError, TargetConnectionError):
                raise
            except Exception as error:
                raise ProposalSetError("proposal policy resolution failed") from error
            # ``type(policy) is str`` is an exact-type test over the declared
            # ``ResolvedMetadataPolicy | str`` union, so each arm is already
            # proven; mypy simply does not narrow inside a conditional
            # expression.
            try:
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
            except ProposalValueError:
                raise
            except Exception as error:
                raise ProposalSetError("proposal evaluation failed") from error
            try:
                current = self._require_current(report)
            except (ProposalSetError, TargetConnectionError):
                raise
            except Exception as error:
                raise ProposalSetError("proposal currency check failed") from error
            if current is not True:
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
            # Policy resolution, evaluation and the currency check now name
            # themselves where they run, so what is left under this handler is
            # the stored-report read and the publication pair -- and only they
            # can reach it. The message is therefore true of what failed rather
            # than of the outcome alone: a resolver that raises before anything
            # was published no longer reports a failed publication. What the
            # message still rules out is the one thing above it: the domain
            # parsers name their malformed-input failures, those are matched
            # already, and a fault that reached here is not a valid-input
            # problem and must not be reported as one.
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
