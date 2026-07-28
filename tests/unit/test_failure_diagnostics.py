from __future__ import annotations

import unittest
from dataclasses import dataclass

from mechanical_librarian.application.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    provider_attempt_diagnostics,
    reviewer_failure_diagnostic,
)


@dataclass(frozen=True)
class _Attempt:
    category: str
    outcome: str
    private_body: str = "credential=/private/library/secret"


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
        valid = reviewer_failure_diagnostic("runtime").to_json()
        malformed = (
            valid[:-1]
            + ',"category":"unknown"}'
        )
        cases = (
            valid.replace('"spawn_runtime"', '"new_failure"'),
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

    def test_reviewer_codes_map_to_same_closed_contract_and_unknown_fails_closed(
        self,
    ) -> None:
        self.assertEqual(
            FailureCategory.CITATION_MEMBERSHIP,
            reviewer_failure_diagnostic("semantic_validation").category,
        )
        self.assertEqual(
            FailureCategory.CURRENTNESS,
            reviewer_failure_diagnostic("currentness").category,
        )
        self.assertFalse(reviewer_failure_diagnostic("currentness").current)
        self.assertEqual(
            FailureCategory.UNKNOWN,
            reviewer_failure_diagnostic("exception:/tmp/secret").category,
        )


if __name__ == "__main__":
    unittest.main()
