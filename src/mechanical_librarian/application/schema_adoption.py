"""Closed vocabulary for explicit pre-release schema adoption."""

from mechanical_librarian.application.metadata_report import MetadataReportError

REVIEW_OWNER_DECISION_ACTIONS_V1 = "review-owner-decision-actions-v1"
OWNER_SETTINGS_V1 = "owner-settings-v1"
REVIEW_SESSION_DECOUPLING_V1 = "review-session-decoupling-v1"
SCHEMA_ADOPTION_PLANS = (
    REVIEW_OWNER_DECISION_ACTIONS_V1,
    OWNER_SETTINGS_V1,
    REVIEW_SESSION_DECOUPLING_V1,
)


class SchemaAdoptionRecoveryRequired(MetadataReportError):
    """The source may have committed; recovery must start from its backup."""
