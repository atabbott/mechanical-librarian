"""Closed vocabulary for explicit pre-release schema adoption."""

from mechanical_librarian.application.metadata_report import MetadataReportError

REVIEW_FIELD_FACTS_V1 = "review-field-facts-v1"
SCHEMA_ADOPTION_PLANS = (REVIEW_FIELD_FACTS_V1,)


class SchemaAdoptionRecoveryRequired(MetadataReportError):
    """The source may have committed; recovery must start from its backup."""
