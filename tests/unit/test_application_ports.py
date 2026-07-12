from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mechanical_librarian.application.ports import (
    Capability,
    CapabilitySet,
    CompatibilityReport,
    CompatibilityStatus,
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    HealthReport,
    HealthStatus,
    OperationContext,
    Page,
    PageRequest,
    PortFailure,
    PortResult,
    ResourceBounds,
    RetryAdvice,
    SafeSummary,
    TimeProbe,
    Unit,
)
from mechanical_librarian.domain.identifiers import AdapterVersion, CompatibilityVersion, ServiceVersion


class CancellationOwner:
    def __init__(self) -> None:
        self.requested = False

    @property
    def probe(self) -> CancellationProbe:
        return CancellationProbe(CancellationId("request-1"), lambda: self.requested)

    def is_cancelled(self) -> bool:
        return self.requested


class PortContractTests(unittest.TestCase):
    def test_result_requires_exactly_one_outcome(self) -> None:
        with self.assertRaises(ValueError):
            PortResult()
        with self.assertRaises(ValueError):
            PortResult(
                value="value",
                failure=PortFailure(
                    FailureKind.TIMEOUT,
                    RetryAdvice(True),
                    SafeSummary("timed out"),
                ),
            )
        self.assertTrue(PortResult(value="value").value)

    def test_unit_is_the_success_value_for_no_payload_operations(self) -> None:
        self.assertIs(PortResult.success(Unit.VALUE).value, Unit.VALUE)
        with self.assertRaises(ValueError):
            PortResult.success(None)

    def test_result_rejects_failure_subclasses_and_untyped_leaks(self) -> None:
        class FailureSubclass(PortFailure):
            pass

        failure = PortFailure(FailureKind.TIMEOUT, RetryAdvice(True), SafeSummary("operation timed out"))
        for unsafe in ("token=secret", object(), FailureSubclass(failure.kind, failure.retry, failure.safe_summary)):
            with self.subTest(unsafe=type(unsafe).__name__), self.assertRaises(ValueError):
                PortResult(failure=unsafe)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                PortResult.failed(unsafe)  # type: ignore[arg-type]

    def test_page_limits_are_bounded(self) -> None:
        for limit in (0, 501):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    PageRequest(None, limit)
        with self.assertRaises(ValueError):
            Page(tuple(range(501)), None)

    def test_failure_retry_and_summary_are_validated(self) -> None:
        retry = RetryAdvice(True, timedelta(seconds=5))
        self.assertTrue(retry.retryable)
        with self.assertRaises(ValueError):
            RetryAdvice(False, timedelta(seconds=1))
        with self.assertRaises(ValueError):
            PortFailure(
                FailureKind.CONFLICT,
                RetryAdvice(True),
                SafeSummary("stale state"),
            )
        with self.assertRaises(ValueError):
            SafeSummary("Authorization: Bearer secret")

    def test_safe_summary_golden_boundary_rejects_credentials_urls_and_paths(self) -> None:
        unsafe = (
            "Authorization: Bearer example-credential",
            "api_key=example-credential",
            "token: example-credential",
            "client_secret=example-credential",
            "password=example-credential",
            "Cookie: session=example-credential",
            "https://user:password@example.test/status",
            "https://example.test/status?token=secret",
            "/Users/alice/private/library",
            r"C:\Users\alice\private\library",
            "~/private/library",
        )
        for value in unsafe:
            with self.subTest(value=value), self.assertRaises(ValueError):
                SafeSummary(value)
        for value in ("service healthy", "versions compatible", "backup verified", "review evidence incomplete", "notification queued"):
            summary = SafeSummary(value)
            self.assertEqual(str(summary), value)
            self.assertNotIn(value, repr(summary))

        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            HealthReport(HealthStatus.HEALTHY, now, "service healthy")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            CompatibilityReport(
                CompatibilityVersion("ports-v1"),
                AdapterVersion("adapter-v1"),
                ServiceVersion("service-v1"),
                CompatibilityStatus.COMPATIBLE,
                "versions compatible",  # type: ignore[arg-type]
            )

    def test_operation_context_is_bounded_and_timezone_aware(self) -> None:
        now = datetime.now(timezone.utc)
        context = OperationContext(
            Deadline(now + timedelta(seconds=30)),
            CancellationOwner().probe,
            ResourceBounds(10, 1024),
            TimeProbe(lambda: now),
        )
        self.assertFalse(context.cancelled)
        with self.assertRaises(ValueError):
            Deadline(datetime.now())
        with self.assertRaises(ValueError):
            ResourceBounds(0, 1024)

    def test_capability_declarations_validate_dependencies(self) -> None:
        capabilities = CapabilitySet(
            frozenset({Capability.METADATA_READ, Capability.METADATA_WRITE})
        )
        self.assertIn(Capability.METADATA_WRITE, capabilities.supported)
        with self.assertRaises(ValueError):
            CapabilitySet(frozenset({Capability.COVER_WRITE}))


if __name__ == "__main__":
    unittest.main()
