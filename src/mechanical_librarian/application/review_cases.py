"""Admit exact current ProposalSets into immutable undecided review cases."""
from __future__ import annotations
from collections.abc import Callable
from typing import Protocol
from mechanical_librarian.domain.proposal_set import ProposalSet, ProposalValueError
from mechanical_librarian.domain.review_case import ReviewAdmissionResult

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
    def __init__(self, repository: ReviewCaseRepository, require_current: Callable[[ProposalSet], bool]) -> None:
        self._repository, self._require_current = repository, require_current
    def run(self, proposal_set_id: str) -> str:
        try:
            proposal = ProposalSet.from_json(self._repository.get_proposal(proposal_set_id))
            if proposal.proposal_set_id != proposal_set_id:
                raise ReviewCaseError("incompatible")
            if self._require_current(proposal) is not True:
                return ReviewAdmissionResult("stale", None).to_json()
            return self._repository.admit_review_case(proposal)
        except ReviewCaseError: raise
        except Exception as error:
            raise ReviewCaseError("persistence") from error

class GetReviewCase:
    def __init__(self, repository: ReviewCaseRepository) -> None: self._repository = repository
    def run(self, review_case_id: str) -> str:
        try: return self._repository.get_review_case(review_case_id)
        except Exception as error: raise ReviewCaseError("review case is unavailable") from error

__all__ = ["AdmitReviewCase","GetReviewCase","ReviewCaseError","ReviewCaseRepository"]
