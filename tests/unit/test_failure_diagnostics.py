from __future__ import annotations

import unittest
from dataclasses import dataclass

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


class FailureDiagnosticTests(unittest.TestCase):
    def test_category_vocabulary_is_exact_and_closed(self) -> None:
        self.assertEqual(
            {
                "authentication",
                "cancellation",
                "citation_membership",
                "currentness",
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
            },
            {value.value for value in FailureCategory},
        )

    def test_closed_diagnostic_round_trips_only_bounded_fields(self) -> None:
        diagnostic = FailureDiagnostic(
            FailureCategory.TRANSPORT,
            FailureComponent.PROVIDER,
            FailureStage.REQUEST,
            retryable=True,
            count=2,
        )

        serialized = diagnostic.to_json()

        self.assertEqual(diagnostic, FailureDiagnostic.from_json(serialized))
        self.assertEqual(
            {
                "category",
                "component",
                "count",
                "current",
                "retryable",
                "stage",
            },
            set(diagnostic.to_data()),
        )
        for forbidden in ("credential", "private", "secret", "body", "path"):
            self.assertNotIn(forbidden, serialized)

    def test_document_rejects_unknown_duplicate_and_noncanonical_fields(self) -> None:
        valid = FailureDiagnostic(
            FailureCategory.TRANSPORT,
            FailureComponent.PROVIDER,
            FailureStage.REQUEST,
        ).to_json()
        malformed = (
            valid[:-1]
            + ',"category":"unknown"}'
        )
        cases = (
            valid.replace('"transport"', '"new_failure"'),
            malformed,
            valid + "\n",
            '{"category":"unknown"}',
            valid.replace('"retryable":null', '"retryable":1'),
        )

        for serialized in cases:
            with self.subTest(serialized=serialized):
                with self.assertRaises(ValueError):
                    FailureDiagnostic.from_json(serialized)

    def test_provider_attempts_are_redacted_and_stably_aggregated(self) -> None:
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

        self.assertEqual(
            (
                FailureCategory.HTTP_NON_SUCCESS,
                FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
                FailureCategory.PROVIDER_DECLARED_ERROR,
                FailureCategory.TRANSPORT,
            ),
            tuple(value.category for value in diagnostics),
        )
        self.assertEqual((2, 1, 1, 1), tuple(value.count for value in diagnostics))
        serialized = "".join(value.to_json() for value in diagnostics)
        for forbidden in ("credential", "private", "secret", "body", "ordinal"):
            self.assertNotIn(forbidden, serialized)

    def test_malformed_optional_attempt_records_fail_closed_to_unknown(self) -> None:
        diagnostics = provider_attempt_diagnostics(
            (
                object(),
                _MalformedAttempt(),
                _Attempt("http_503", "terminal"),
            )
        )

        self.assertEqual(
            (
                FailureCategory.HTTP_NON_SUCCESS,
                FailureCategory.UNKNOWN,
            ),
            tuple(value.category for value in diagnostics),
        )
        self.assertEqual((1, 2), tuple(value.count for value in diagnostics))
        malformed_collection = provider_attempt_diagnostics(
            [_Attempt("http_503", "terminal")]
        )
        self.assertEqual(1, len(malformed_collection))
        self.assertEqual(
            FailureCategory.UNKNOWN, malformed_collection[0].category
        )
        self.assertNotIn(
            "credential",
            "".join(value.to_json() for value in diagnostics),
        )

    def test_incomplete_causes_are_closed_truthful_and_redacted(self) -> None:
        diagnostics = provider_attempt_diagnostics(
            (),
            status="incomplete",
            incomplete_causes=(
                _IncompleteCause("deadline"),
                _IncompleteCause("aggregate_bytes"),
                _IncompleteCause("dispatch_count"),
            ),
        )

        self.assertEqual(
            (
                FailureCategory.LOOKUP_CONSTRUCTION,
                FailureCategory.MALFORMED_OVER_BOUND_RESPONSE,
                FailureCategory.TIMEOUT,
            ),
            tuple(value.category for value in diagnostics),
        )
        self.assertNotIn(
            FailureCategory.TIMEOUT,
            tuple(value.category for value in diagnostics[:2]),
        )
        serialized = "".join(value.to_json() for value in diagnostics)
        for forbidden in ("credential", "private", "secret", "query", "path"):
            self.assertNotIn(forbidden, serialized)

    def test_incomplete_causes_fail_closed_when_absent_or_malformed(self) -> None:
        for causes in (
            (),
            (_IncompleteCause("credential=/private/library/secret"),),
            [_IncompleteCause("deadline")],
        ):
            with self.subTest(causes=type(causes).__name__):
                diagnostics = provider_attempt_diagnostics(
                    (), status="incomplete", incomplete_causes=causes
                )
                self.assertEqual(
                    (FailureCategory.UNKNOWN,),
                    tuple(value.category for value in diagnostics),
                )

    def test_provider_signals_have_exact_production_mappings(
        self,
    ) -> None:
        def provider(
            category: str = "none",
            *,
            status: str | None = None,
        ) -> FailureDiagnostic:
            values = provider_attempt_diagnostics(
                (_Attempt(category, "terminal"),),
                status=status,
            )
            self.assertEqual(1, len(values))
            return values[0]

        signals = {
            "provider.lookup_limit": provider(
                status="query_limit_exceeded"
            ),
            "provider.graphql_errors": provider("provider_declared_error"),
            "provider.http": provider("http_503"),
            "provider.quota": provider("http_429"),
            "provider.no_match": provider(status="no_match"),
            "provider.response_malformed": provider("response_malformed"),
            "provider.transport": provider("transport_connect_error"),
            "provider.deadline": provider("deadline"),
            "provider.cancellation": provider("cancellation"),
        }
        expected = {
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
        self.assertEqual(set(expected), set(signals))
        for signal, diagnostic in signals.items():
            with self.subTest(signal=signal):
                self.assertEqual(
                    expected[signal],
                    (
                        diagnostic.category,
                        diagnostic.component,
                        diagnostic.stage,
                    ),
                )


if __name__ == "__main__":
    unittest.main()
