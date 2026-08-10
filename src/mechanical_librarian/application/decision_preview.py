"""Provider-independent preview of the delivered deterministic decision engine."""

from __future__ import annotations

from typing import Protocol

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.shadow_decision import (
    ShadowDecisionError,
    ShadowDecisionSet,
    decide_shadow_packet,
)
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket


class DecisionPreviewError(RuntimeError):
    """The deterministic preview is unavailable or no longer current."""


class DecisionPreviewRepository(Protocol):
    def preview_shadow_packet(self, review_case_id: str) -> ShadowReviewPacket: ...


def preview_shadow_decision(
    repository: DecisionPreviewRepository,
    review_case_id: str,
) -> ShadowDecisionSet:
    try:
        return decide_shadow_packet(repository.preview_shadow_packet(review_case_id))
    except MetadataReportError as error:
        raise DecisionPreviewError("persistence") from error
    except ShadowDecisionError as error:
        raise DecisionPreviewError("currentness") from error


__all__ = ["DecisionPreviewError", "DecisionPreviewRepository", "preview_shadow_decision"]
