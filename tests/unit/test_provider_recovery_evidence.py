"""Direct unit cover for the relocated provider-recovery evidence decisions.

The composition root re-exports every one of these under its former private
name, so the existing suites still reach them there.  These cases address the
application module itself, which is where the decisions now live.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import cast

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    CurrentnessError,
    MetadataReportError,
    TargetNotFoundError,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    TargetConnectionHeld,
    classify_provider_recovery_failure,
    provider_recovery_jobs,
    provider_statuses,
    record_provider_recovery_observations,
)
from mechanical_librarian.application.provider_retry_successor import (
    ProviderRetryDisabled,
    ProviderRetrySharedFailure,
    ProviderRetrySuccessorError,
)
from mechanical_librarian.application.review_pipeline import ReviewPipelineError
from mechanical_librarian.domain.metadata_report import MetadataReport, ProviderStatus

_CONFIGURATION = ApplicationConfiguration(
    (TargetConnectionConfiguration("primary", "https://example.invalid", "/run/token"),),
    (
        LibraryConfiguration(
            "books", "primary", "library-1", "ebook", "mount",
            ("catalogue-a", "catalogue-b"),
        ),
    ),
    (MediaMountConfiguration("mount", "/container", "/service", True),),
    (
        ProviderConnectionConfiguration(
            "catalogue-a", True, False, "/run/hardcover", "hardcover"
        ),
        ProviderConnectionConfiguration(
            "catalogue-b", True, False, None, "open_library",
            "owner@example.invalid",
        ),
    ),
    provider_recovery_generation=4,
)


def _report(*observations: object) -> MetadataReport:
    value = SimpleNamespace(
        report_id="report-1",
        target=SimpleNamespace(connection="primary", library="books", item="item-1"),
        file=SimpleNamespace(identity="file-1"),
        providers=observations,
    )
    return cast("MetadataReport", value)


def _observation(provider: str, status: ProviderStatus, *lanes: object) -> object:
    return SimpleNamespace(provider=provider, status=status, lanes=lanes)


def _lane(identity: str, status: ProviderStatus) -> object:
    return SimpleNamespace(identity=identity, status=status)


class _Observer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int, str, int]] = []

    def observe_failure(
        self, *, connection: str, adapter: str, scope: str, generation: int,
        failure: str, now: int,
    ) -> None:
        self.calls.append((connection, adapter, scope, generation, failure, now))


class ClassifyProviderRecoveryFailureTests(unittest.TestCase):
    def test_every_recognised_failure_shape_keeps_its_classification(self) -> None:
        credential_caused = ProviderRetrySuccessorError("boom")
        credential_caused.__cause__ = CredentialError("credential")
        unrelated_caused = ProviderRetrySuccessorError("boom")
        unrelated_caused.__cause__ = ValueError("value")
        expected: tuple[tuple[Exception, tuple[str, int | None]], ...] = (
            (TargetConnectionHeld("held"), ("target_held", None)),
            (CredentialError("credential"), ("authentication", None)),
            (credential_caused, ("authentication", None)),
            (unrelated_caused, ("terminal", None)),
            (ProviderRetryDisabled("disabled"), ("disabled", None)),
            (ProviderRetrySharedFailure("unavailable"), ("transient", None)),
            (ProviderRetrySharedFailure("incomplete", 900), ("transient", 900)),
            (ProviderRetrySharedFailure("rate_limited", 1), ("rate_limited", 1)),
            (
                ProviderRetrySharedFailure("authentication"),
                ("authentication", None),
            ),
            (ProviderRetrySharedFailure("authorization"), ("authorization", None)),
            (
                ProviderRetrySharedFailure("configuration", 86_400),
                ("configuration", 86_400),
            ),
            (
                ProviderRetrySuccessorError("provider retry configuration changed"),
                ("configuration", None),
            ),
            (ProviderRetrySuccessorError("provider retry failed"), ("terminal", None)),
            (CurrentnessError("stale"), ("stale", None)),
            (TargetNotFoundError("absent"), ("stale", None)),
            (MetadataReportError("report"), ("transient", None)),
            (RuntimeError("runtime"), ("transient", None)),
        )
        for error, classification in expected:
            with self.subTest(error=type(error).__name__, message=str(error)):
                self.assertEqual(
                    classification, classify_provider_recovery_failure(error)
                )

    def test_target_hold_outranks_the_currentness_it_inherits(self) -> None:
        held = TargetConnectionHeld("held")
        self.assertIsInstance(held, CurrentnessError)
        self.assertEqual(("target_held", None), classify_provider_recovery_failure(held))
        self.assertEqual(
            ("stale", None),
            classify_provider_recovery_failure(CurrentnessError("held")),
        )


class ProviderStatusesTests(unittest.TestCase):
    def test_observation_status_precedes_its_lanes_in_declaration_order(self) -> None:
        report = _report(
            _observation(
                "catalogue-a", ProviderStatus.INCOMPLETE,
                _lane("b-lane", ProviderStatus.CANDIDATES),
                _lane("a-lane", ProviderStatus.RATE_LIMITED),
            ),
            _observation("catalogue-b", ProviderStatus.DISABLED),
        )
        self.assertEqual(
            (
                ProviderStatus.INCOMPLETE,
                ProviderStatus.CANDIDATES,
                ProviderStatus.RATE_LIMITED,
                ProviderStatus.DISABLED,
            ),
            provider_statuses(report),
        )


class RecordProviderRecoveryObservationsTests(unittest.TestCase):
    def test_credential_failure_outranks_a_transient_one_on_the_same_provider(self) -> None:
        observer = _Observer()
        record_provider_recovery_observations(
            observer,
            _report(
                _observation(
                    "catalogue-a", ProviderStatus.UNAVAILABLE,
                    _lane("lane", ProviderStatus.AUTHORIZATION),
                ),
            ),
            adapters={"catalogue-a": "hardcover"},
            scope="books", generation=4, now=7,
        )
        self.assertEqual(
            [("catalogue-a", "hardcover", "books", 4, "authorization", 7)],
            observer.calls,
        )

    def test_non_terminal_providers_are_never_observed(self) -> None:
        observer = _Observer()
        record_provider_recovery_observations(
            observer,
            _report(
                _observation(
                    "catalogue-a", ProviderStatus.CANDIDATES,
                    _lane("lane", ProviderStatus.CANDIDATES),
                ),
                _observation("catalogue-b", ProviderStatus.DISABLED),
            ),
            adapters={"catalogue-a": "hardcover", "catalogue-b": "open_library"},
            scope="books", generation=4, now=7,
        )
        self.assertEqual([], observer.calls)

    def test_unconfigured_adapter_is_a_configuration_failure(self) -> None:
        observer = _Observer()
        with self.assertRaises(ReviewPipelineError) as raised:
            record_provider_recovery_observations(
                observer,
                _report(_observation("unknown", ProviderStatus.RATE_LIMITED)),
                adapters={}, scope="books", generation=4, now=7,
            )
        self.assertEqual("configuration", raised.exception.code)
        self.assertEqual([], observer.calls)


class ProviderRecoveryJobsTests(unittest.TestCase):
    def test_only_retryable_lanes_become_canonically_ordered_work(self) -> None:
        jobs = provider_recovery_jobs(
            _report(
                _observation(
                    "catalogue-b", ProviderStatus.CANDIDATES,
                    _lane("z-lane", ProviderStatus.RATE_LIMITED),
                    _lane("a-lane", ProviderStatus.INCOMPLETE),
                    _lane("skipped", ProviderStatus.CANDIDATES),
                ),
                _observation(
                    "catalogue-a", ProviderStatus.UNAVAILABLE,
                    _lane("only", ProviderStatus.AUTHENTICATION),
                ),
                _observation("quiet", ProviderStatus.CANDIDATES),
            ),
            package=(None, "report-1", None, None, "report_only"),
            configuration=_CONFIGURATION,
            scope="books",
        )
        self.assertEqual(("catalogue-a", "catalogue-b"), tuple(job.connection for job in jobs))
        self.assertEqual(("hardcover", "open_library"), tuple(job.adapter for job in jobs))
        self.assertEqual(json.dumps(["only"], separators=(",", ":")), jobs[0].lane_ids)
        self.assertEqual(
            json.dumps(["a-lane", "z-lane"], separators=(",", ":")), jobs[1].lane_ids
        )
        self.assertEqual({4}, {job.generation for job in jobs})
        self.assertEqual({"report-1"}, {job.member_id for job in jobs})

    def test_a_report_with_no_retryable_lane_creates_no_work(self) -> None:
        self.assertEqual(
            (),
            provider_recovery_jobs(
                _report(
                    _observation(
                        "catalogue-a", ProviderStatus.AUTHENTICATION,
                        _lane("lane", ProviderStatus.CANDIDATES),
                    ),
                ),
                package=(None, "report-1", None, None, "report_only"),
                configuration=_CONFIGURATION,
                scope="books",
            ),
        )

    def test_unconfigured_provider_is_a_configuration_failure(self) -> None:
        with self.assertRaises(ReviewPipelineError) as raised:
            provider_recovery_jobs(
                _report(
                    _observation(
                        "unknown", ProviderStatus.UNAVAILABLE,
                        _lane("lane", ProviderStatus.UNAVAILABLE),
                    ),
                ),
                package=(None, "report-1", None, None, "report_only"),
                configuration=_CONFIGURATION,
                scope="books",
            )
        self.assertEqual("configuration", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
