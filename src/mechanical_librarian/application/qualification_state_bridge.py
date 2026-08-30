"""Closed vocabulary for explicit maintainer qualification-state bridging."""

from mechanical_librarian.application.metadata_report import MetadataReportError

RETAINED_LIBRARY_V26_TO_V32 = "retained-library-v26-to-v32"
QUALIFICATION_STATE_BRIDGE_PLANS = (RETAINED_LIBRARY_V26_TO_V32,)


class QualificationStateBridgeRecoveryRequired(MetadataReportError):
    """A durable output may exist and must be reconciled before retrying."""
