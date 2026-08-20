"""Closed vocabulary for explicit pre-release schema adoption."""

from mechanical_librarian.application.metadata_report import MetadataReportError

REVIEW_READ_INDEXES_V1 = "review-read-indexes-v1"
SCHEMA_ADOPTION_PLANS = (REVIEW_READ_INDEXES_V1,)


class SchemaAdoptionRecoveryRequired(MetadataReportError):
    """The source may have committed; recovery must start from its backup."""
