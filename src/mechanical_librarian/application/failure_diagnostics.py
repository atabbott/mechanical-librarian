"""Stable, aggregate-safe failure diagnostics for application boundaries."""

from __future__ import annotations

from collections import Counter

from mechanical_librarian.domain.failure_diagnostics import (
    MAX_DIAGNOSTIC_COUNT,
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    diagnostics_are_canonical,
)


_REVIEWER_FAILURES = {
    "runtime": (
        FailureCategory.SPAWN_RUNTIME,
        FailureStage.EXECUTION,
    ),
    "setup": (
        FailureCategory.SETUP_CONFIGURATION,
        FailureStage.SETUP,
    ),
    "auth": (
        FailureCategory.AUTHENTICATION,
        FailureStage.AUTHENTICATION,
    ),
    "profile": (
        FailureCategory.SETUP_CONFIGURATION,
        FailureStage.SETUP,
    ),
    "timeout": (
        FailureCategory.TIMEOUT,
        FailureStage.EXECUTION,
    ),
    "cancel": (
        FailureCategory.CANCELLATION,
        FailureStage.EXECUTION,
    ),
    "schema": (
        FailureCategory.SCHEMA_CONSTRUCTION,
        FailureStage.SCHEMA,
    ),
    "response_format": (
        FailureCategory.LIVE_SCHEMA_REJECTION,
        FailureStage.SCHEMA,
    ),
    "malformed_output": (
        FailureCategory.MALFORMED_OUTPUT,
        FailureStage.RESPONSE,
    ),
    "semantic_validation": (
        FailureCategory.CITATION_MEMBERSHIP,
        FailureStage.VALIDATION,
    ),
    "identity": (
        FailureCategory.IDENTITY,
        FailureStage.IDENTITY,
    ),
    "currentness": (
        FailureCategory.CURRENTNESS,
        FailureStage.CURRENTNESS,
    ),
    "persistence": (
        FailureCategory.PERSISTENCE,
        FailureStage.PERSISTENCE,
    ),
}


def reviewer_failure_diagnostic(code: str, *, count: int = 0) -> FailureDiagnostic:
    """Map a process-local reviewer code to the stable application taxonomy."""

    category, stage = _REVIEWER_FAILURES.get(
        code,
        (FailureCategory.UNKNOWN, FailureStage.EXECUTION),
    )
    bounded_count = (
        max(1, min(count, MAX_DIAGNOSTIC_COUNT))
        if type(count) is int
        else 1
    )
    component = (
        FailureComponent.PERSISTENCE
        if category is FailureCategory.PERSISTENCE
        else FailureComponent.REVIEWER
    )
    return FailureDiagnostic(
        category,
        component,
        stage,
        current=False if category is FailureCategory.CURRENTNESS else None,
        count=bounded_count,
    )


def provider_attempt_diagnostics(
    attempts: object,
    *,
    status: str | None = None,
) -> tuple[FailureDiagnostic, ...]:
    """Discard attempt payload details and return deterministic bounded aggregates."""

    counts: Counter[
        tuple[FailureCategory, FailureStage, bool | None]
    ] = Counter()
    if type(attempts) is tuple:
        bounded_attempts: tuple[object, ...] = attempts
    else:
        bounded_attempts = ()
        status = "unknown"
    for attempt in bounded_attempts:
        try:
            category = getattr(attempt, "category", None)
            outcome = getattr(attempt, "outcome", None)
        except Exception:
            category = None
            outcome = None
        if type(category) is not str or type(outcome) is not str:
            key = (
                FailureCategory.UNKNOWN,
                FailureStage.RESPONSE,
                None,
            )
        else:
            mapped = _provider_attempt_category(category)
            if mapped is None:
                continue
            category, stage = mapped
            retryable = (
                True
                if outcome in {"retry", "exhausted"}
                else (
                    False
                    if outcome in {"terminal", "cancelled", "deadline_exhausted"}
                    else None
                )
            )
            key = (category, stage, retryable)
        counts[key] = min(counts[key] + 1, MAX_DIAGNOSTIC_COUNT)
    status_mapping = {
        "authentication": (
            FailureCategory.AUTHENTICATION,
            FailureStage.RESPONSE,
            False,
        ),
        "authorization": (
            FailureCategory.AUTHENTICATION,
            FailureStage.RESPONSE,
            False,
        ),
        "rate_limited": (
            FailureCategory.QUOTA_RATE_LIMIT,
            FailureStage.RESPONSE,
            None,
        ),
        "no_match": (
            FailureCategory.EMPTY_NO_MATCH,
            FailureStage.RESPONSE,
            False,
        ),
        "malformed": (
            FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
            FailureStage.RESPONSE,
            False,
        ),
        "oversized": (
            FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
            FailureStage.RESPONSE,
            False,
        ),
        "candidate_limit_exceeded": (
            FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
            FailureStage.RESPONSE,
            False,
        ),
        "query_limit_exceeded": (
            FailureCategory.LOOKUP_CONSTRUCTION,
            FailureStage.LOOKUP,
            False,
        ),
        "unavailable": (
            FailureCategory.UNKNOWN,
            FailureStage.RESPONSE,
            None,
        ),
        "unknown": (
            FailureCategory.UNKNOWN,
            FailureStage.RESPONSE,
            None,
        ),
    }
    required = status_mapping.get(status)
    if (
        required is not None
        and (required[0] is not FailureCategory.UNKNOWN or not counts)
        and not any(category is required[0] for category, _, _ in counts)
    ):
        counts[required] = 1
    return tuple(
        FailureDiagnostic(
            category,
            FailureComponent.PROVIDER,
            stage,
            retryable=retryable,
            count=count,
        )
        for (category, stage, retryable), count in sorted(
            counts.items(),
            key=lambda item: (
                item[0][0].value,
                item[0][1].value,
                -1 if item[0][2] is None else int(item[0][2]),
            ),
        )
    )


def _provider_attempt_category(
    category: str,
) -> tuple[FailureCategory, FailureStage] | None:
    if category == "none":
        return None
    if category == "cancellation":
        return FailureCategory.CANCELLATION, FailureStage.EXECUTION
    if category == "deadline" or category.endswith("_timeout"):
        return FailureCategory.TIMEOUT, FailureStage.REQUEST
    if category.startswith("transport_"):
        return FailureCategory.TRANSPORT, FailureStage.REQUEST
    if category in {"http_401", "http_403"}:
        return FailureCategory.AUTHENTICATION, FailureStage.RESPONSE
    if category == "http_429":
        return FailureCategory.QUOTA_RATE_LIMIT, FailureStage.RESPONSE
    if category.startswith("http_"):
        return FailureCategory.HTTP_NON_SUCCESS, FailureStage.RESPONSE
    if category in {"response_malformed", "response_oversized"}:
        return (
            FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
            FailureStage.RESPONSE,
        )
    if category == "provider_declared_error":
        return FailureCategory.PROVIDER_DECLARED_ERROR, FailureStage.RESPONSE
    if category == "provider_failure":
        return FailureCategory.UNKNOWN, FailureStage.RESPONSE
    return FailureCategory.UNKNOWN, FailureStage.RESPONSE


__all__ = [
    "FailureCategory",
    "FailureComponent",
    "FailureDiagnostic",
    "FailureStage",
    "MAX_DIAGNOSTIC_COUNT",
    "diagnostics_are_canonical",
    "provider_attempt_diagnostics",
    "reviewer_failure_diagnostic",
]
