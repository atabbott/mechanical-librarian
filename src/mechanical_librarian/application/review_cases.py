"""Admit exact current ProposalSets into immutable undecided review cases."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.metadata_report import CurrentnessError
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionIncompatibleError,
    ReviewAdmissionResult,
    ReviewAdmissionStaleError,
)


class ReviewCaseError(RuntimeError):
    """A value-free production-review admission failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReviewCaseRepository(Protocol):
    def get_proposal(self, proposal_set_id: str) -> str: ...
    def admit_review_case(self, proposal_set: ProposalSet) -> str: ...
    def get_review_case(self, review_case_id: str) -> str: ...
    def close(self) -> None: ...


class AdmitReviewCase:
    """Admit one exact current ProposalSet, or say why it cannot be admitted.

    Both seams are injected and untyped: ``ReviewCaseRepository`` is a protocol
    with no declared error type, and ``require_current`` is a callable that
    reaches the target through whatever the composition root bound. Every
    admission outcome a caller acts on -- a lost connection, staleness, an
    incompatible proposal -- is matched by type ahead of the two broad
    handlers, and they convert what is left with the cause chained rather than
    admitting the case. The outer one is a last resort over the whole run, so
    what it converts is a failure of either seam that neither named and also a
    stored proposal document this layer could not reopen; ``persistence`` is
    the code it answers with for all of them, the store having produced the
    document as well as the fault.
    """

    def __init__(
        self,
        repository: ReviewCaseRepository,
        require_current: Callable[[ProposalSet], bool],
    ) -> None:
        self._repository = repository
        self._require_current = require_current

    def run(self, proposal_set_id: str) -> str:
        try:
            proposal = ProposalSet.from_json(
                self._repository.get_proposal(proposal_set_id)
            )
            if proposal.proposal_set_id != proposal_set_id:
                raise ReviewCaseError("incompatible")
            try:
                current = self._require_current(proposal)
            except TargetConnectionError:
                raise
            except CurrentnessError:
                return ReviewAdmissionResult("stale", None).to_json()
            except ReviewCaseError:
                raise
            except Exception as error:
                # A currentness check that did not finish has not shown the
                # proposal to be current, and must not be read as either the
                # ``stale`` answer above or a licence to admit.
                raise ReviewCaseError("currentness") from error
            if current is not True:
                return ReviewAdmissionResult("stale", None).to_json()
            return self._repository.admit_review_case(proposal)
        except TargetConnectionError:
            raise
        except (CurrentnessError, ReviewAdmissionStaleError):
            return ReviewAdmissionResult("stale", None).to_json()
        except ReviewAdmissionIncompatibleError as error:
            raise ReviewCaseError("incompatible") from error
        except ReviewCaseError:
            raise
        except Exception as error:
            raise ReviewCaseError("persistence") from error


class GetReviewCase:
    def __init__(self, repository: ReviewCaseRepository) -> None:
        self._repository = repository

    def run(self, review_case_id: str) -> str:
        try:
            return self._repository.get_review_case(review_case_id)
        except Exception as error:
            # One protocol call with no declared error type: absence and fault
            # are indistinguishable here, and the caller is owed a typed
            # refusal rather than the adapter's own exception.
            raise ReviewCaseError("persistence") from error


__all__ = [
    "AdmitReviewCase",
    "GetReviewCase",
    "ReviewCaseError",
    "ReviewCaseRepository",
]
