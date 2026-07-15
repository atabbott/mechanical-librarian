"""Application contract for the closed F10a synthetic review-case report."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mechanical_librarian.domain.title_proposals import TitleProposalResult
from mechanical_librarian.domain.title_review_cases import SyntheticTitleReviewCase


SYNTHETIC_TITLE_REVIEW_REPORT_SCHEMA = "synthetic-title-review-case-report-v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class TitleReviewCaseRunError(RuntimeError):
    """Synthetic review-case inspection failed safely."""


class ReviewTransitionKind(StrEnum):
    CREATED = "created"
    SUPERSEDED = "superseded"
    RETIRED = "retired"
    NO_CASE = "no_case"


@dataclass(frozen=True)
class ReviewAdmission:
    source_set_id: str
    source_result: TitleProposalResult
    current_case: SyntheticTitleReviewCase | None
    previous_case: SyntheticTitleReviewCase | None
    current_case_id: str | None
    transition_id: str
    transition_kind: ReviewTransitionKind
    transition_reason: str
    replayed: bool
    case_history_count: int
    transition_count: int

    def __post_init__(self) -> None:
        if (
            not _IDENTIFIER.fullmatch(self.source_set_id)
            or type(self.source_result) is not TitleProposalResult
            or (self.current_case is not None and type(self.current_case) is not SyntheticTitleReviewCase)
            or (self.previous_case is not None and type(self.previous_case) is not SyntheticTitleReviewCase)
            or (self.current_case_id is not None and not _IDENTIFIER.fullmatch(self.current_case_id))
            or not _IDENTIFIER.fullmatch(self.transition_id)
            or type(self.transition_kind) is not ReviewTransitionKind
            or self.transition_reason not in {
                "admit current synthetic review case",
                "supersede current synthetic review case",
                "retire current synthetic review case",
                "record current synthetic no-case result",
            }
            or type(self.replayed) is not bool
            or type(self.case_history_count) is not int
            or self.case_history_count < 0
            or type(self.transition_count) is not int
            or self.transition_count < 1
        ):
            raise ValueError("review admission projection is invalid")
        if (self.current_case is None) != (self.current_case_id is None):
            raise ValueError("review admission current case is inconsistent")
        if self.current_case is not None and self.current_case.case_id != self.current_case_id:
            raise ValueError("review admission current pointer is inconsistent")
        if (self.source_result is TitleProposalResult.NEEDS_REVIEW) != (self.current_case is not None):
            raise ValueError("review admission result is inconsistent")
        if self.transition_kind is ReviewTransitionKind.SUPERSEDED and self.previous_case is None:
            raise ValueError("review supersession lacks its predecessor")
        if self.transition_kind is ReviewTransitionKind.CREATED and (
            self.previous_case is not None or self.current_case is None
        ):
            raise ValueError("review creation projection is invalid")
        if self.transition_kind is ReviewTransitionKind.RETIRED and (
            self.previous_case is None or self.current_case is not None
        ):
            raise ValueError("review retirement projection is invalid")
        if self.transition_kind is ReviewTransitionKind.NO_CASE and (
            self.previous_case is not None or self.current_case is not None
        ):
            raise ValueError("review no-case projection is invalid")
        expected_reason = {
            ReviewTransitionKind.CREATED: "admit current synthetic review case",
            ReviewTransitionKind.SUPERSEDED: "supersede current synthetic review case",
            ReviewTransitionKind.RETIRED: "retire current synthetic review case",
            ReviewTransitionKind.NO_CASE: "record current synthetic no-case result",
        }[self.transition_kind]
        if self.transition_reason != expected_reason:
            raise ValueError("review transition reason is inconsistent")


class TitleReviewCaseRepository(Protocol):
    def admit_current(self, lineage: str) -> ReviewAdmission: ...


@dataclass(frozen=True)
class SyntheticTitleReviewCaseReport:
    admission: ReviewAdmission

    def __post_init__(self) -> None:
        if type(self.admission) is not ReviewAdmission:
            raise ValueError("review-case report requires a typed admission")

    def to_data(self) -> dict[str, object]:
        admission = self.admission
        return {
            "authority": "synthetic-report-only",
            "case": None if admission.current_case is None else admission.current_case.to_data(),
            "outcome": "current_case" if admission.current_case is not None else "no_case",
            "persistence": {
                "case_history_count": admission.case_history_count,
                "current_case_id": admission.current_case_id,
                "replayed": admission.replayed,
                "transition_count": admission.transition_count,
                "transition_id": admission.transition_id,
                "transition_kind": admission.transition_kind.value,
                "transition_reason": admission.transition_reason,
            },
            "previous_case": (
                None if admission.previous_case is None else admission.previous_case.to_data()
            ),
            "production_applicable": False,
            "schema": SYNTHETIC_TITLE_REVIEW_REPORT_SCHEMA,
            "source": {
                "result": admission.source_result.value,
                "set_id": admission.source_set_id,
            },
        }

    def to_json(self) -> str:
        rendered = json.dumps(
            self.to_data(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(rendered.encode("utf-8")) > 393_216:
            raise ValueError("review-case report is too large")
        return rendered


def render_title_review_case_text(report: SyntheticTitleReviewCaseReport) -> str:
    if type(report) is not SyntheticTitleReviewCaseReport:
        raise ValueError("review-case text rendering requires a typed report")
    return "".join(
        f"{key}="
        + json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
        for key, value in report.to_data().items()
    )


class InspectSyntheticTitleReviewCase:
    def __init__(self, repository: TitleReviewCaseRepository) -> None:
        self._repository = repository

    def run(self, lineage: str) -> SyntheticTitleReviewCaseReport:
        if type(lineage) is not str or _IDENTIFIER.fullmatch(lineage) is None:
            raise TitleReviewCaseRunError("synthetic review lineage is invalid")
        try:
            admission = self._repository.admit_current(lineage)
            if type(admission) is not ReviewAdmission:
                raise TitleReviewCaseRunError("review persistence readback failed")
            report = SyntheticTitleReviewCaseReport(admission)
            report.to_json()
            render_title_review_case_text(report)
            return report
        except TitleReviewCaseRunError:
            raise
        except Exception:
            raise TitleReviewCaseRunError("synthetic review case failed safely") from None


__all__ = [
    "InspectSyntheticTitleReviewCase",
    "ReviewAdmission",
    "ReviewTransitionKind",
    "SYNTHETIC_TITLE_REVIEW_REPORT_SCHEMA",
    "SyntheticTitleReviewCaseReport",
    "TitleReviewCaseRepository",
    "TitleReviewCaseRunError",
    "render_title_review_case_text",
]
