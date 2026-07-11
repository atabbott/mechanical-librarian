"""Closed public-safe values shared by audit contracts and persistence."""

from __future__ import annotations


SAFE_AUDIT_SUMMARIES = frozenset(
    {
        "apply completed",
        "apply failed",
        "apply started",
        "apply verified",
        "authorization denied",
        "authorization granted",
        "authorization revoked",
        "backup failed",
        "backup verified",
        "decision recorded",
        "discovered item",
        "item changed",
        "item discovered",
        "item skipped",
        "item unresolved",
        "job completed",
        "job failed",
        "job queued",
        "job retrying",
        "job started",
        "plan created",
        "plan validated",
        "review approved",
        "review rejected",
        "review requested",
        "rollback completed",
        "rollback failed",
        "rollback started",
        "verification failed",
        "verification succeeded",
    }
)


def validated_safe_audit_summary(value: object) -> str | None:
    """Return an exact safe summary or reject the value without exposing it."""

    return value if type(value) is str and value in SAFE_AUDIT_SUMMARIES else None
