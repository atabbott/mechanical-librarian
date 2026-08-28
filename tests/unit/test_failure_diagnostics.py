from __future__ import annotations

from dataclasses import dataclass

import pytest

from mechanical_librarian.application.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    provider_attempt_diagnostics,
)


@dataclass(frozen=True)
class _Attempt:
    category: str
    outcome: str
    private_body: str = "credential=/private/library/secret"


class _MalformedAttempt:
    @property
    def category(self):
        raise RuntimeError("credential=/private/library/secret")

    @property
    def outcome(self):
        raise RuntimeError("credential=/private/library/secret")


@dataclass(frozen=True)
class _IncompleteCause:
    cause: object
    private_query: str = "credential=/private/library/secret"


_VALID_DOCUMENT = FailureDiagnostic(
    FailureCategory.TRANSPORT,
    FailureComponent.PROVIDER,
    FailureStage.REQUEST,
).to_json()

# The signal name each production mapping is reached through, expressed as the
# attempt category and provider status that produce it.
_PROVIDER_SIGNALS: dict[str, tuple[str, str | None]] = {
    "provider.lookup_limit": ("none", "query_limit_exceeded"),
    "provider.graphql_errors": ("provider_declared_error", None),
    "provider.http": ("http_503", None),
    "provider.quota": ("http_429", None),
    "provider.no_match": ("none", "no_match"),
    "provider.response_malformed": ("response_malformed", None),
    "provider.transport": ("transport_connect_error", None),
    "provider.deadline": ("deadline", None),
    "provider.cancellation": ("cancellation", None),
}

_EXPECTED_PROVIDER_MAPPINGS: dict[
    str, tuple[FailureCategory, FailureComponent, FailureStage]
] = {
    "provider.lookup_limit": (
        FailureCategory.LOOKUP_CONSTRUCTION,
        FailureComponent.PROVIDER,
        FailureStage.LOOKUP,
    ),
    "provider.graphql_errors": (
        FailureCategory.PROVIDER_DECLARED_ERROR,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.http": (
        FailureCategory.HTTP_NON_SUCCESS,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.quota": (
        FailureCategory.QUOTA_RATE_LIMIT,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.no_match": (
        FailureCategory.EMPTY_NO_MATCH,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.response_malformed": (
        FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.transport": (
        FailureCategory.TRANSPORT,
        FailureComponent.PROVIDER,
        FailureStage.REQUEST,
    ),
    "provider.deadline": (
        FailureCategory.TIMEOUT,
        FailureComponent.PROVIDER,
        FailureStage.REQUEST,
    ),
    "provider.cancellation": (
        FailureCategory.CANCELLATION,
        FailureComponent.PROVIDER,
        FailureStage.EXECUTION,
    ),
}


def test_category_vocabulary_is_exact_and_closed() -> None:
    assert {
        "authentication",
        "burst_throttle",
        "cancellation",
        "citation_membership",
        "currentness",
        "daily_quota_exhausted",
        "empty_no_match",
        "http_non_success",
        "identity",
        "live_schema_rejection",
        "lookup_construction",
        "malformed_output",
        "malformed_over_bound_response",
        "persistence",
        "provider_declared_error",
        "quota_rate_limit",
        "schema_construction",
        "setup_configuration",
        "spawn_runtime",
        "timeout",
        "transport",
        "unknown",
    } == {value.value for value in FailureCategory}


def test_specific_rate_limit_evidence_does_not_gain_a_generic_duplicate() -> None:
    diagnostics = provider_attempt_diagnostics(
        (_Attempt("burst_throttled", "terminal"),), status="rate_limited"
    )
    assert [FailureCategory.BURST_THROTTLE] == [
        diagnostic.category for diagnostic in diagnostics
    ]


def test_closed_diagnostic_round_trips_only_bounded_fields() -> None:
    diagnostic = FailureDiagnostic(
        FailureCategory.TRANSPORT,
        FailureComponent.PROVIDER,
        FailureStage.REQUEST,
        retryable=True,
        count=2,
    )

    serialized = diagnostic.to_json()

    assert diagnostic == FailureDiagnostic.from_json(serialized)
    assert {
        "category",
        "component",
        "count",
        "current",
        "retryable",
        "stage",
    } == set(diagnostic.to_data())
    for forbidden in ("credential", "private", "secret", "body", "path"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "serialized",
    (
        _VALID_DOCUMENT.replace('"transport"', '"new_failure"'),
        _VALID_DOCUMENT[:-1] + ',"category":"unknown"}',
        _VALID_DOCUMENT + "\n",
        '{"category":"unknown"}',
        _VALID_DOCUMENT.replace('"retryable":null', '"retryable":1'),
    ),
    ids=(
        "unknown_category",
        "duplicate_key",
        "trailing_newline",
        "absent_fields",
        "noncanonical_retryable",
    ),
)
def test_document_rejects_unknown_duplicate_and_noncanonical_fields(
    serialized: str,
) -> None:
    with pytest.raises(ValueError):
        FailureDiagnostic.from_json(serialized)


def test_provider_attempts_are_redacted_and_stably_aggregated() -> None:
    diagnostics = provider_attempt_diagnostics(
        (
            _Attempt("http_503", "retry"),
            _Attempt("none", "response_received"),
            _Attempt("http_503", "exhausted"),
            _Attempt("response_malformed", "terminal"),
            _Attempt("provider_declared_error", "terminal"),
            _Attempt("transport_connect_error", "retry"),
        )
    )

    assert (
        FailureCategory.HTTP_NON_SUCCESS,
        FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
        FailureCategory.PROVIDER_DECLARED_ERROR,
        FailureCategory.TRANSPORT,
    ) == tuple(value.category for value in diagnostics)
    assert (2, 1, 1, 1) == tuple(value.count for value in diagnostics)
    serialized = "".join(value.to_json() for value in diagnostics)
    for forbidden in ("credential", "private", "secret", "body", "ordinal"):
        assert forbidden not in serialized


def test_malformed_optional_attempt_records_fail_closed_to_unknown() -> None:
    diagnostics = provider_attempt_diagnostics(
        (
            object(),
            _MalformedAttempt(),
            _Attempt("http_503", "terminal"),
        )
    )

    assert (
        FailureCategory.HTTP_NON_SUCCESS,
        FailureCategory.UNKNOWN,
    ) == tuple(value.category for value in diagnostics)
    assert (1, 2) == tuple(value.count for value in diagnostics)
    malformed_collection = provider_attempt_diagnostics(
        [_Attempt("http_503", "terminal")]
    )
    assert 1 == len(malformed_collection)
    assert FailureCategory.UNKNOWN == malformed_collection[0].category
    assert "credential" not in "".join(value.to_json() for value in diagnostics)


def test_incomplete_causes_are_closed_truthful_and_redacted() -> None:
    diagnostics = provider_attempt_diagnostics(
        (),
        status="incomplete",
        incomplete_causes=(
            _IncompleteCause("deadline"),
            _IncompleteCause("aggregate_bytes"),
            _IncompleteCause("dispatch_count"),
        ),
    )

    assert (
        FailureCategory.LOOKUP_CONSTRUCTION,
        FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
        FailureCategory.TIMEOUT,
    ) == tuple(value.category for value in diagnostics)
    assert FailureCategory.TIMEOUT not in tuple(
        value.category for value in diagnostics[:2]
    )
    serialized = "".join(value.to_json() for value in diagnostics)
    for forbidden in ("credential", "private", "secret", "query", "path"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "causes",
    (
        (),
        (_IncompleteCause("credential=/private/library/secret"),),
        [_IncompleteCause("deadline")],
    ),
    ids=("absent", "unrecognised_cause", "unbounded_collection"),
)
def test_incomplete_causes_fail_closed_when_absent_or_malformed(
    causes: object,
) -> None:
    diagnostics = provider_attempt_diagnostics(
        (), status="incomplete", incomplete_causes=causes
    )
    assert (FailureCategory.UNKNOWN,) == tuple(value.category for value in diagnostics)


def test_provider_signal_mappings_cover_every_declared_signal() -> None:
    assert set(_EXPECTED_PROVIDER_MAPPINGS) == set(_PROVIDER_SIGNALS)


@pytest.mark.parametrize("signal", tuple(_PROVIDER_SIGNALS))
def test_provider_signals_have_exact_production_mappings(signal: str) -> None:
    category, status = _PROVIDER_SIGNALS[signal]
    values = provider_attempt_diagnostics(
        (_Attempt(category, "terminal"),),
        status=status,
    )
    assert 1 == len(values)
    diagnostic = values[0]
    assert _EXPECTED_PROVIDER_MAPPINGS[signal] == (
        diagnostic.category,
        diagnostic.component,
        diagnostic.stage,
    )
