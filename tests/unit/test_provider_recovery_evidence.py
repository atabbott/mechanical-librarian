"""Direct unit cover for the relocated provider-recovery evidence decisions.

The composition root re-exports every one of these under its former private
name, so the existing suites still reach them there.  These cases address the
application module itself, which is where the decisions now live.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

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
    provider_budget_dispatch_reason,
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
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
    _result,
)
from tests.support.proposal_evidence import (
    _report as _proposal_report,
)

_CONFIGURATION = ApplicationConfiguration(
    (
        TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/run/token"
        ),
    ),
    (
        LibraryConfiguration(
            "books",
            "primary",
            "library-1",
            "ebook",
            "mount",
            ("catalogue-a", "catalogue-b"),
        ),
    ),
    (MediaMountConfiguration("mount", "/container", "/service", True),),
    (
        ProviderConnectionConfiguration(
            "catalogue-a", True, False, "/run/hardcover", "hardcover"
        ),
        ProviderConnectionConfiguration(
            "catalogue-b",
            True,
            False,
            None,
            "open_library",
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


def _proposal(reason: str, *, report_id: str = "report-1") -> ProposalSet:
    """Make the smallest exact-type projection needed by this classifier test."""
    proposal = object.__new__(ProposalSet)
    object.__setattr__(proposal, "report_id", report_id)
    object.__setattr__(
        proposal,
        "results",
        (
            SimpleNamespace(
                outcome=ProposalOutcome.UNRESOLVED_CHOICE,
                reasons=(reason,),
                alternatives=(
                    SimpleNamespace(evidence_references=("provider_" + "a" * 64,)),
                ),
            ),
        ),
    )
    return proposal


class _Observer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int, str, int, int | None]] = []

    def observe_failure(
        self,
        *,
        connection: str,
        adapter: str,
        scope: str,
        generation: int,
        failure: str,
        now: int,
        retry_after: int | None = None,
    ) -> None:
        self.calls.append(
            (connection, adapter, scope, generation, failure, now, retry_after)
        )


def _recognised_failures() -> dict[str, tuple[Exception, tuple[str, int | None]]]:
    """Every failure shape the classifier recognises, named by its shape."""

    credential_caused = ProviderRetrySuccessorError("boom")
    credential_caused.__cause__ = CredentialError("credential")
    unrelated_caused = ProviderRetrySuccessorError("boom")
    unrelated_caused.__cause__ = ValueError("value")
    return {
        "target_held": (TargetConnectionHeld("held"), ("target_held", None)),
        "credential_error": (CredentialError("credential"), ("authentication", None)),
        "credential_cause": (credential_caused, ("authentication", None)),
        "unrelated_cause": (unrelated_caused, ("terminal", None)),
        "retry_disabled": (ProviderRetryDisabled("disabled"), ("disabled", None)),
        "shared_unavailable": (
            ProviderRetrySharedFailure("unavailable"),
            ("transient", None),
        ),
        "shared_incomplete": (
            ProviderRetrySharedFailure("incomplete", 900),
            ("transient", 900),
        ),
        "shared_rate_limited": (
            ProviderRetrySharedFailure("rate_limited", 1),
            ("rate_limited", 1),
        ),
        "shared_authentication": (
            ProviderRetrySharedFailure("authentication"),
            ("authentication", None),
        ),
        "shared_authorization": (
            ProviderRetrySharedFailure("authorization"),
            ("authorization", None),
        ),
        "shared_configuration": (
            ProviderRetrySharedFailure("configuration", 86_400),
            ("configuration", 86_400),
        ),
        "successor_configuration": (
            ProviderRetrySuccessorError("provider retry configuration changed"),
            ("configuration", None),
        ),
        "successor_failed": (
            ProviderRetrySuccessorError("provider retry failed"),
            ("terminal", None),
        ),
        "currentness": (CurrentnessError("stale"), ("stale", None)),
        "target_not_found": (TargetNotFoundError("absent"), ("stale", None)),
        "report_error": (MetadataReportError("report"), ("transient", None)),
        "runtime_error": (RuntimeError("runtime"), ("transient", None)),
    }


_RECOGNISED_FAILURES = _recognised_failures()


@pytest.mark.parametrize(
    ("report", "proposal", "expected"),
    (
        (
            _report(SimpleNamespace(candidates=("candidate",), lanes=())),
            _proposal("insufficient_independent_corroboration"),
            ("missing_corroboration", 0),
        ),
        (
            _report(SimpleNamespace(candidates=(), lanes=())),
            None,
            ("zero_provider_evidence", 1),
        ),
        (
            _report(SimpleNamespace(candidates=("candidate",), lanes=())),
            _proposal("competing_evidence"),
            ("settleable_disagreement", 2),
        ),
        (
            _report(SimpleNamespace(candidates=("candidate",), lanes=())),
            None,
            ("blanket_scan", 3),
        ),
    ),
    ids=("corroboration", "zero-evidence", "disagreement", "fallback"),
)
def test_provider_budget_dispatch_reason_has_closed_value_order(
    report: MetadataReport, proposal: ProposalSet | None, expected: tuple[str, int]
) -> None:
    assert expected == provider_budget_dispatch_reason(
        report, proposal, report.providers[0]
    )


def test_provider_budget_dispatch_reason_fails_closed_on_mismatched_projection() -> (
    None
):
    with pytest.raises(ReviewPipelineError, match="projection"):
        provider_budget_dispatch_reason(
            _report(),
            _proposal("competing_evidence", report_id="other"),
            SimpleNamespace(candidates=(), lanes=()),
        )


def test_provider_budget_reason_prioritises_the_provider_that_can_add_independence() -> (
    None
):
    report = _report()
    proposal = _proposal("insufficient_independent_corroboration")
    sole_authority = SimpleNamespace(
        candidates=(
            SimpleNamespace(
                claims=(SimpleNamespace(reference="provider_" + "a" * 64),),
            ),
        ),
        lanes=(),
    )
    independent_provider = SimpleNamespace(candidates=(), lanes=())
    assert ("blanket_scan", 3) == provider_budget_dispatch_reason(
        report, proposal, sole_authority
    )
    assert ("missing_corroboration", 0) == provider_budget_dispatch_reason(
        report, proposal, independent_provider
    )


def test_fixed_budget_replay_counts_actual_decided_fields_not_reason_labels() -> None:
    """The same two requests settle a real field only in the ranked replay."""
    initial_title = _provider(
        "first",
        "edition-1",
        (
            _claim(
                "first",
                "edition-1",
                ClaimKind.TITLE,
                "Corrected title",
                scope=ClaimScope.WORK,
            ),
        ),
        authority="hardcover",
    )
    initial_subtitle = _provider(
        "second",
        "edition-2",
        (
            _claim(
                "second",
                "edition-2",
                ClaimKind.SUBTITLE,
                "Corrected subtitle",
                scope=ClaimScope.WORK,
            ),
        ),
        authority="hardcover",
    )
    title_support = _provider(
        "third",
        "edition-3",
        (
            _claim(
                "third",
                "edition-3",
                ClaimKind.TITLE,
                "Corrected title",
                scope=ClaimScope.WORK,
            ),
        ),
        authority="open_library",
    )
    subtitle_support = _provider(
        "fourth",
        "edition-4",
        (
            _claim(
                "fourth",
                "edition-4",
                ClaimKind.SUBTITLE,
                "Corrected subtitle",
                scope=ClaimScope.WORK,
            ),
        ),
        authority="open_library",
    )
    fields = _fields()

    def decided(providers: tuple[ProviderObservation, ...]) -> int:
        proposal = evaluate_report(_proposal_report(fields=fields, providers=providers))
        return sum(
            _result(proposal, destination).outcome is ProposalOutcome.PROPOSE_CHANGE
            for destination in (TargetDestination.TITLE, TargetDestination.SUBTITLE)
        )

    # The prior member order spends its two starts on recoveries that reproduce
    # the original authority. Ranked recovery spends the same budget on the two
    # independent successor observations, then evaluates the real proposal set.
    assert 0 == decided((initial_title, initial_subtitle))
    assert 1 == decided(
        (initial_title, initial_subtitle, title_support, subtitle_support)
    )


@pytest.mark.parametrize(
    ("error", "classification"),
    tuple(_RECOGNISED_FAILURES.values()),
    ids=tuple(_RECOGNISED_FAILURES),
)
def test_every_recognised_failure_shape_keeps_its_classification(
    error: Exception, classification: tuple[str, int | None]
) -> None:
    assert classification == classify_provider_recovery_failure(error)


def test_target_hold_outranks_the_currentness_it_inherits() -> None:
    held = TargetConnectionHeld("held")
    assert isinstance(held, CurrentnessError)
    assert ("target_held", None) == classify_provider_recovery_failure(held)
    assert ("stale", None) == classify_provider_recovery_failure(
        CurrentnessError("held")
    )


def test_observation_status_precedes_its_lanes_in_declaration_order() -> None:
    report = _report(
        _observation(
            "catalogue-a",
            ProviderStatus.INCOMPLETE,
            _lane("b-lane", ProviderStatus.CANDIDATES),
            _lane("a-lane", ProviderStatus.RATE_LIMITED),
        ),
        _observation("catalogue-b", ProviderStatus.DISABLED),
    )
    assert (
        ProviderStatus.INCOMPLETE,
        ProviderStatus.CANDIDATES,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.DISABLED,
    ) == provider_statuses(report)


def test_credential_failure_outranks_a_transient_one_on_the_same_provider() -> None:
    observer = _Observer()
    record_provider_recovery_observations(
        observer,
        _report(
            _observation(
                "catalogue-a",
                ProviderStatus.UNAVAILABLE,
                _lane("lane", ProviderStatus.AUTHORIZATION),
            ),
        ),
        adapters={"catalogue-a": "hardcover"},
        scope="books",
        generation=4,
        now=7,
    )
    assert [
        ("catalogue-a", "hardcover", "books", 4, "authorization", 7, None)
    ] == observer.calls


def test_initial_report_uses_exact_burst_evidence_and_provider_window() -> None:
    observer = _Observer()
    observation = ProviderObservation(
        "catalogue-a",
        ProviderStatus.RATE_LIMITED,
        authority="google_books",
        diagnostics=(
            FailureDiagnostic(
                FailureCategory.BURST_THROTTLE,
                FailureComponent.PROVIDER,
                FailureStage.RESPONSE,
            ),
        ),
    )
    record_provider_recovery_observations(
        observer,
        _report(observation),
        adapters={"catalogue-a": "google_books"},
        scope="books",
        generation=4,
        now=7,
    )
    assert [
        ("catalogue-a", "google_books", "books", 4, "burst_throttled", 7, 100)
    ] == observer.calls


def test_initial_report_keeps_specific_plus_generic_rate_limit_evidence_ambiguous() -> (
    None
):
    observer = _Observer()
    specific = FailureDiagnostic(
        FailureCategory.BURST_THROTTLE,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    )
    generic = FailureDiagnostic(
        FailureCategory.QUOTA_RATE_LIMIT,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    )
    observation = SimpleNamespace(
        provider="catalogue-a",
        status=ProviderStatus.RATE_LIMITED,
        authority="google_books",
        diagnostics=(specific,),
        lanes=(
            SimpleNamespace(
                status=ProviderStatus.RATE_LIMITED,
                diagnostics=(generic,),
            ),
        ),
    )
    record_provider_recovery_observations(
        observer,
        _report(observation),
        adapters={"catalogue-a": "google_books"},
        scope="books",
        generation=4,
        now=7,
    )
    assert [
        ("catalogue-a", "google_books", "books", 4, "rate_limited", 7, None)
    ] == observer.calls


def test_non_terminal_providers_are_never_observed() -> None:
    observer = _Observer()
    record_provider_recovery_observations(
        observer,
        _report(
            _observation(
                "catalogue-a",
                ProviderStatus.CANDIDATES,
                _lane("lane", ProviderStatus.CANDIDATES),
            ),
            _observation("catalogue-b", ProviderStatus.DISABLED),
        ),
        adapters={"catalogue-a": "hardcover", "catalogue-b": "open_library"},
        scope="books",
        generation=4,
        now=7,
    )
    assert [] == observer.calls


def test_unconfigured_adapter_is_a_configuration_failure() -> None:
    observer = _Observer()
    with pytest.raises(ReviewPipelineError) as raised:
        record_provider_recovery_observations(
            observer,
            _report(_observation("unknown", ProviderStatus.RATE_LIMITED)),
            adapters={},
            scope="books",
            generation=4,
            now=7,
        )
    assert "configuration" == raised.value.code
    assert [] == observer.calls


def test_only_retryable_lanes_become_canonically_ordered_work() -> None:
    jobs = provider_recovery_jobs(
        _report(
            _observation(
                "catalogue-b",
                ProviderStatus.CANDIDATES,
                _lane("z-lane", ProviderStatus.RATE_LIMITED),
                _lane("a-lane", ProviderStatus.INCOMPLETE),
                _lane("skipped", ProviderStatus.CANDIDATES),
            ),
            _observation(
                "catalogue-a",
                ProviderStatus.UNAVAILABLE,
                _lane("only", ProviderStatus.AUTHENTICATION),
            ),
            _observation("quiet", ProviderStatus.CANDIDATES),
        ),
        package=(None, "report-1", None, None, "report_only"),
        configuration=_CONFIGURATION,
        scope="books",
    )
    assert ("catalogue-a", "catalogue-b") == tuple(job.connection for job in jobs)
    assert ("hardcover", "open_library") == tuple(job.adapter for job in jobs)
    assert json.dumps(["only"], separators=(",", ":")) == jobs[0].lane_ids
    assert json.dumps(["a-lane", "z-lane"], separators=(",", ":")) == jobs[1].lane_ids
    assert {4} == {job.generation for job in jobs}
    assert {"report-1"} == {job.member_id for job in jobs}


def test_a_report_with_no_retryable_lane_creates_no_work() -> None:
    assert () == provider_recovery_jobs(
        _report(
            _observation(
                "catalogue-a",
                ProviderStatus.AUTHENTICATION,
                _lane("lane", ProviderStatus.CANDIDATES),
            ),
        ),
        package=(None, "report-1", None, None, "report_only"),
        configuration=_CONFIGURATION,
        scope="books",
    )


def test_unconfigured_provider_is_a_configuration_failure() -> None:
    with pytest.raises(ReviewPipelineError) as raised:
        provider_recovery_jobs(
            _report(
                _observation(
                    "unknown",
                    ProviderStatus.UNAVAILABLE,
                    _lane("lane", ProviderStatus.UNAVAILABLE),
                ),
            ),
            package=(None, "report-1", None, None, "report_only"),
            configuration=_CONFIGURATION,
            scope="books",
        )
    assert "configuration" == raised.value.code


def test_an_insufficient_input_audiobook_lane_records_no_provider_health() -> None:
    """A provider asked something it cannot answer is not an unhealthy provider.

    Audnexus resolves a product and never a search, so a plan with no ASIN lane
    leaves it with nothing to look up. That is a fact about the item, and
    recording it against the provider's recovery health would take a working
    provider out of service for having been asked the wrong question.
    """
    observer = _Observer()
    record_provider_recovery_observations(
        observer,
        _report(
            ProviderObservation(
                "enrichment",
                ProviderStatus.INSUFFICIENT_INPUT,
                authority="audnexus",
            )
        ),
        adapters={"enrichment": "audnexus"},
        scope="audiobooks",
        generation=4,
        now=7,
    )

    assert [] == observer.calls
