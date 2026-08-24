"""Closed vocabulary for explicit pre-release schema adoption."""

from mechanical_librarian.application.metadata_report import MetadataReportError

REVIEW_OWNER_DECISION_ACTIONS_V1 = "review-owner-decision-actions-v1"
SCHEMA_ADOPTION_PLANS = (REVIEW_OWNER_DECISION_ACTIONS_V1,)


class SchemaAdoptionRecoveryRequired(MetadataReportError):
    """The source may have committed; recovery must start from its backup."""
