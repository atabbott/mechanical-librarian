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


def provider_attempt_diagnostics(
    attempts: object,
    *,
    status: str | None = None,
    incomplete_causes: object = (),
) -> tuple[FailureDiagnostic, ...]:
    """Discard attempt payload details and return deterministic bounded aggregates."""

    counts: Counter[tuple[FailureCategory, FailureStage, bool | None]] = Counter()
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
            # ``attempt`` is whatever a provider adapter put in its records, so
            # these may be computed properties with no declared error type. A
            # missing attribute already yields ``None``; only a property that
            # fails reaches here, and ``None`` routes it to the same unknown
            # bucket as a value of the wrong type on the next line. Nothing is
            # dropped -- every attempt is still counted.
            category = None
            outcome = None
        key: tuple[FailureCategory, FailureStage, bool | None]
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
    status_mapping: dict[
        str | None, tuple[FailureCategory, FailureStage, bool | None]
    ] = {
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
    invalid_incomplete_causes = False
    if status == "incomplete":
        if type(incomplete_causes) is not tuple:
            invalid_incomplete_causes = True
        else:
            cause_mapping = {
                "deadline": (FailureCategory.TIMEOUT, FailureStage.REQUEST),
                "aggregate_bytes": (
                    FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
                    FailureStage.RESPONSE,
                ),
                "dispatch_count": (
                    FailureCategory.LOOKUP_CONSTRUCTION,
                    FailureStage.LOOKUP,
                ),
            }
            for record in incomplete_causes:
                try:
                    cause = getattr(record, "cause", None)
                except Exception:
                    # Same adapter-supplied record and the same reasoning as
                    # the attempt loop. ``None`` fails the mapping below, which
                    # marks the causes invalid rather than silently accepting
                    # an aggregate built from a cause it could not read.
                    cause = None
                mapped = cause_mapping.get(cause) if isinstance(cause, str) else None
                if mapped is None:
                    invalid_incomplete_causes = True
                    continue
                counts[(mapped[0], mapped[1], False)] = min(
                    counts[(mapped[0], mapped[1], False)] + 1,
                    MAX_DIAGNOSTIC_COUNT,
                )
    required = status_mapping.get(status)
    if (
        required is not None
        and (required[0] is not FailureCategory.UNKNOWN or not counts)
        and not any(category is required[0] for category, _, _ in counts)
    ):
        counts[required] = 1
    if status == "incomplete" and (invalid_incomplete_causes or not incomplete_causes):
        counts[(FailureCategory.UNKNOWN, FailureStage.RESPONSE, None)] = 1
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


def merge_provider_diagnostics(
    *groups: tuple[FailureDiagnostic, ...],
) -> tuple[FailureDiagnostic, ...]:
    """Canonically coalesce bounded provider aggregates without replay inflation."""

    counts: Counter[tuple[FailureCategory, FailureStage, bool | None]] = Counter()
    for group in groups:
        if not diagnostics_are_canonical(group):
            raise ValueError("provider diagnostics are invalid")
        for value in group:
            if (
                value.component is not FailureComponent.PROVIDER
                or value.current is not None
            ):
                raise ValueError("provider diagnostics are invalid")
            key = (value.category, value.stage, value.retryable)
            counts[key] = min(counts[key] + value.count, MAX_DIAGNOSTIC_COUNT)
    return tuple(
        FailureDiagnostic(
            category, FailureComponent.PROVIDER, stage, retryable=retryable, count=count
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
    "merge_provider_diagnostics",
    "provider_attempt_diagnostics",
]
