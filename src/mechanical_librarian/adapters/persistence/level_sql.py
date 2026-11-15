"""The Book matching gate's SQL fragments (#1355, S10, amendment 5).

Every gate compares a case's reconciled level with the configured one: the
executor's deferral, the re-check's selection and counts, the decision gate
and the review API's ``checking``. They read only the ``reconciled_level``
columns and the configured level, never an offer, so the gate cannot drift
from one reader to another. No imports, so the schema can use them.
"""

from __future__ import annotations


def _reconciled_level_sql(case: str) -> str:
    """The level the case ``case`` names was last reconciled at (#1355, S10).

    ``COALESCE(marker, offer row level, 'identifier')``: the marker first,
    since reconciliation always writes it; a case with neither was never
    reconciled past ``identifier``. Only the ``reconciled_level`` columns are
    read, never an offer.
    """
    return (
        "COALESCE((SELECT level_marker.reconciled_level "
        "FROM review_case_level_reconciliation level_marker "
        f"WHERE level_marker.review_case_id={case}),"
        "(SELECT CASE WHEN count(*)=1 THEN max(level_offer.reconciled_level) END "
        "FROM review_case_level_offers level_offer "
        f"WHERE level_offer.review_case_id={case}),'identifier')"
    )


# The configured Book matching level the gates compare with (amendment 5).
_TARGET_LEVEL_SQL = (
    "COALESCE((SELECT level_target.work_identity_level "
    "FROM review_preparation_control level_target "
    "WHERE level_target.singleton=1),'identifier')"
)


def _unreconciled_case_sql(case: str) -> str:
    """Whether the case ``case`` names waits for its Book matching check."""
    return "(" + _reconciled_level_sql(case) + "<>" + _TARGET_LEVEL_SQL + ")"
