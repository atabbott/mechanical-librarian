"""Closed vocabulary for explicit pre-release schema adoption (ADR-0013)."""

from mechanical_librarian.application.metadata_report import MetadataReportError

REVIEW_ATTENTION_FACTS_V1 = "review-attention-facts-v1"
SCHEMA_ADOPTION_PLANS = (REVIEW_ATTENTION_FACTS_V1,)


class SchemaAdoptionRecoveryRequired(MetadataReportError):
    """The source may have committed; recovery must start from its backup."""
