"""Direct unit cover for the relocated provider-recovery evidence decisions.

The composition root re-exports every one of these under its former private
name, so the existing suites still reach them there.  These cases address the
application module itself, which is where the decisions now live.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
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
    ProviderFailureSnapshot,
    ProviderLaneResult,
    ProviderObservationFailure,
    TargetNotFoundError,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.application.provider_recovery_activation import (
    ProviderRecoveryActivation,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    TargetConnectionHeld,
    classify_provider_recovery_failure,
    provider_budget_dispatch_reason,
    provider_recovery_jobs,
    provider_statuses,
    record_provider_recovery_observations,
)
from mechanical_librarian.application.provider_recovery_runner import (
    ScopedProviderRecoveryRunner,
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
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderRequestAccounting,
    ProviderStatus,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from mechanical_librarian.domain.provider_recovery import (
    ProviderRecovery,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
    _publication_local_isbn,
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


def _report(*observations: object, audio: object = None) -> MetadataReport:
    value = SimpleNamespace(
        report_id="report-1",
        target=SimpleNamespace(connection="primary", library="books", item="item-1"),
        file=SimpleNamespace(identity="file-1"),
        providers=observations,
        audio=audio,
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
        _report(sole_authority), proposal, sole_authority
    )
    assert ("missing_corroboration", 0) == provider_budget_dispatch_reason(
        _report(sole_authority, independent_provider), proposal, independent_provider
    )


@pytest.mark.parametrize(
    "evidenced",
    (
        SimpleNamespace(candidates=("candidate",), lanes=()),
        SimpleNamespace(
            candidates=(),
            lanes=(SimpleNamespace(candidates=("lane-candidate",)),),
        ),
    ),
    ids=("provider-candidate", "lane-candidate"),
)
def test_zero_evidence_requires_every_report_provider_to_be_empty(
    evidenced: object,
) -> None:
    recovering = SimpleNamespace(candidates=(), lanes=())
    assert ("blanket_scan", 3) == provider_budget_dispatch_reason(
        _report(recovering, evidenced), None, recovering
    )


def test_fixed_budget_replay_counts_actual_decided_fields_not_reason_labels() -> None:
    """Two persisted, production-path runner starts beat member-id selection."""

    def recovery_case(
        ordinal: int, *, decisive: bool
    ) -> tuple[MetadataReport, MetadataReport]:
        value = f"Corrected title {ordinal}"
        fields = _fields(current_title=f"Current title {ordinal}")
        local_claims = (_publication_local_isbn(),)
        candidate_lane = metadata_discovery_plan(
            TargetSnapshot("ebook", f"Current title {ordinal}", (), fields=fields),
            local_claims,
        ).lanes[0]
        failed_lane = replace(
            candidate_lane,
            identity="lane_" + f"{ordinal + 999:064x}",
        )
        first = _provider(
            "catalogue-a",
            f"edition-a-{ordinal}",
            (
                _claim(
                    "catalogue-a",
                    f"edition-a-{ordinal}",
                    ClaimKind.TITLE,
                    value,
                    scope=ClaimScope.WORK,
                ),
            ),
            authority="hardcover",
        )
        support = _provider(
            "catalogue-b",
            f"edition-b-{ordinal}",
            (
                _claim(
                    "catalogue-b",
                    f"edition-b-{ordinal}",
                    ClaimKind.TITLE,
                    value,
                    scope=ClaimScope.WORK,
                ),
            ),
            authority="open_library",
        )
        recovering = provider_observation(
            "catalogue-b",
            (
                *(
                    ()
                    if decisive
                    else (
                        ProviderLaneResult(
                            candidate_lane,
                            ProviderStatus.CANDIDATES,
                            support.candidates,
                        ),
                    )
                ),
                ProviderLaneResult(failed_lane, ProviderStatus.RATE_LIMITED),
            ),
            authority="open_library",
        )
        base = MetadataReport.create(
            target=TargetFacts("primary", "books", f"item-{ordinal}", fields),
            file=FileFacts(
                0,
                f"{ordinal + 1:064x}",
                f"{ordinal + 101:064x}",
            ),
            local_claims=local_claims,
            local_reasons=("package_metadata_identifier",),
            providers=(first, recovering) if decisive else (recovering,),
        )
        successor = MetadataReport.create(
            target=base.target,
            file=base.file,
            local_claims=local_claims,
            local_reasons=base.local_reasons,
            providers=(first, support) if decisive else (support,),
        )
        return base, successor

    # These four immutable reports pin the old member-id ordering: the two
    # blanket scans sort before the two missing-corroboration packages. The
    # production classifier, rather than the fixture, assigns those reasons.
    # A member id is the report's content digest, which embeds the report
    # schema discriminator, so the two non-decisive ordinals are re-derived
    # here rather than fixed: the pin must hold under whatever schema is
    # current, not under the one it was first written against.
    decisive_cases = (
        recovery_case(52, decisive=True),
        recovery_case(54, decisive=True),
    )
    lowest_decisive = min(base.report_id for base, _ in decisive_cases)
    scan_cases: list[tuple[MetadataReport, MetadataReport]] = []
    ordinal = 0
    while len(scan_cases) < 2:
        ordinal += 1
        candidate = recovery_case(ordinal, decisive=False)
        if candidate[0].report_id < lowest_decisive:
            scan_cases.append(candidate)
    cases = (
        *((base, successor, False) for base, successor in scan_cases),
        *((base, successor, True) for base, successor in decisive_cases),
    )
    successors = {base.report_id: successor for base, successor, _ in cases}

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
        store.record(
            ProviderRecovery.failed(
                connection="catalogue-b",
                adapter="open_library",
                scope="books",
                generation=4,
                failure="rate_limited",
                now=0,
                retry_after=1,
            ),
            occurred_at=0,
        )
        repository = open_repository(Path(directory) / "reports.sqlite")
        activation = ProviderRecoveryActivation(
            configuration=_CONFIGURATION,
            repository_factory=lambda: repository,
            observers=lambda *_: (_ for _ in ()).throw(AssertionError("unused")),
            provider_resolver=lambda *_: None,
            pipeline=lambda: None,
            enqueue=lambda jobs, enqueued_at, outcomes: store.enqueue_jobs(
                jobs, enqueued_at=enqueued_at, outcomes=outcomes
            ),
            clock=lambda: 1,
        )
        for base, _, _ in cases:
            proposal = evaluate_report(base)
            repository.publish(base, lambda: True)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
            package = repository.current_review_package(
                base.target.connection, base.target.library, base.target.item
            )
            assert package is not None
            repository._connection.execute("BEGIN IMMEDIATE")
            repository._recovery_outbox.enqueue(
                base.target.connection,
                base.target.library,
                base.target.item,
                package,
                0,
            )
            repository._connection.execute("COMMIT")
        assert () == activation.drain_outbox(repository)

        actual_jobs = store._connection.execute(
            "SELECT member_id,dispatch_reason FROM provider_recovery_jobs "
            "ORDER BY member_id"
        ).fetchall()
        assert 4 == len(actual_jobs)
        historical = tuple(member_id for member_id, _ in actual_jobs[:2])
        assert {"blanket_scan"} == {reason for _, reason in actual_jobs[:2]}

        decided: list[int] = []
        activated: list[str] = []
        budget = 2

        def retry(_, job, __) -> None:
            activated.append(job.member_id)
            proposal = evaluate_report(successors[job.member_id])
            decided.append(
                sum(
                    result.outcome is ProposalOutcome.PROPOSE_CHANGE
                    for result in proposal.results
                )
            )

        runner = ScopedProviderRecoveryRunner(
            store=store,
            generation=4,
            retry=retry,
            classify_failure=lambda _: ("transient", None),
            cancelled=lambda: len(activated) >= budget,
        )
        assert "fanout" == runner.run_one(now=1, token="ranked").state
        assert budget == len(activated) == len(decided)
        assert {"missing_corroboration"} == {
            reason for member_id, reason in actual_jobs if member_id in activated
        }
        ranked_yield = sum(decided)
        repository.close()
        store.close()

    historical_yield = sum(
        sum(
            result.outcome is ProposalOutcome.PROPOSE_CHANGE
            for result in evaluate_report(successors[member_id]).results
        )
        for member_id in historical
    )
    assert 2 == ranked_yield
    assert 0 == historical_yield
    assert ranked_yield > historical_yield


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


_DISPATCH_SET_ADAPTERS = {
    "books": "google_books",
    "enrichment": "audnexus",
    "inventaire": "inventaire",
    "store": "itunes_search",
}


@pytest.mark.parametrize(
    "audio,recorded",
    (
        (None, ("books", "enrichment", "inventaire", "store")),
        (object(), ("enrichment",)),
    ),
    ids=("ebook", "audiobook"),
)
def test_an_audiobook_report_records_health_only_for_the_dispatch_set(
    audio: object, recorded: tuple[str, ...]
) -> None:
    """A carried-forward excluded observation is no health evidence (#1247).

    A retry successor copies its siblings forward, so an audiobook successor
    over a report published before the dispatch set can still hold a failing
    Google Books, Inventaire or iTunes observation. Recording it would reset
    that provider's library-wide health and delay its ebook recovery.
    """
    observer = _Observer()
    record_provider_recovery_observations(
        observer,
        _report(*_dispatch_set_observations(), audio=audio),
        adapters=_DISPATCH_SET_ADAPTERS,
        scope="books",
        generation=4,
        now=7,
    )

    assert recorded == tuple(call[0] for call in observer.calls)


def test_an_unconfigured_provider_on_an_audiobook_still_records_nothing() -> None:
    observer = _Observer()
    with pytest.raises(ReviewPipelineError) as raised:
        record_provider_recovery_observations(
            observer,
            _report(
                _observation("unknown", ProviderStatus.RATE_LIMITED),
                *_dispatch_set_observations(),
                audio=object(),
            ),
            adapters=_DISPATCH_SET_ADAPTERS,
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


_AUDIO_DISPATCH_CONFIGURATION = replace(
    _CONFIGURATION,
    libraries=(
        replace(
            _CONFIGURATION.libraries[0],
            provider_connections=("books", "enrichment", "inventaire", "store"),
        ),
    ),
    providers=(
        ProviderConnectionConfiguration("books", True, False, None, "google_books"),
        ProviderConnectionConfiguration("enrichment", True, False, None, "audnexus"),
        ProviderConnectionConfiguration("inventaire", True, False, None, "inventaire"),
        ProviderConnectionConfiguration("store", True, False, None, "itunes_search"),
    ),
)


def _dispatch_set_observations() -> tuple[object, ...]:
    return tuple(
        _observation(name, ProviderStatus.UNAVAILABLE, _lane("lane", status))
        for name, status in (
            ("books", ProviderStatus.RATE_LIMITED),
            ("enrichment", ProviderStatus.UNAVAILABLE),
            ("inventaire", ProviderStatus.UNAVAILABLE),
            ("store", ProviderStatus.INCOMPLETE),
        )
    )


@pytest.mark.parametrize(
    "audio,adapters",
    (
        (None, ("google_books", "audnexus", "inventaire", "itunes_search")),
        (object(), ("audnexus",)),
    ),
    ids=("ebook", "audiobook"),
)
def test_an_audiobook_report_enrolls_only_the_dispatch_set(
    audio: object, adapters: tuple[str, ...]
) -> None:
    """A retained audiobook observation outside the set is never retried (#1247).

    Such an observation comes from a report published before the set, or one a
    successor carried forward from it. The same observations on an ebook report
    still enroll every provider.
    """
    jobs = provider_recovery_jobs(
        _report(*_dispatch_set_observations(), audio=audio),
        package=(None, "report-1", None, None, "report_only"),
        configuration=_AUDIO_DISPATCH_CONFIGURATION,
        scope="books",
    )

    assert adapters == tuple(job.adapter for job in jobs)


def test_an_unconfigured_provider_on_an_audiobook_is_still_refused_first() -> None:
    with pytest.raises(ReviewPipelineError) as raised:
        provider_recovery_jobs(
            _report(
                *_dispatch_set_observations(),
                _observation(
                    "unknown",
                    ProviderStatus.UNAVAILABLE,
                    _lane("lane", ProviderStatus.UNAVAILABLE),
                ),
                audio=object(),
            ),
            package=(None, "report-1", None, None, "report_only"),
            configuration=_AUDIO_DISPATCH_CONFIGURATION,
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


@pytest.mark.parametrize(
    "status",
    (
        ProviderStatus.MALFORMED,
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
        ProviderStatus.INSUFFICIENT_INPUT,
        ProviderStatus.NO_MATCH,
    ),
)
def test_inventaire_nontransient_lanes_never_enroll_recovery(
    status: ProviderStatus,
) -> None:
    configuration = replace(
        _CONFIGURATION,
        providers=(
            ProviderConnectionConfiguration(
                "catalogue-a", True, False, None, "inventaire"
            ),
            _CONFIGURATION.providers[1],
        ),
    )
    assert (
        provider_recovery_jobs(
            _report(_observation("catalogue-a", status, _lane("lane", status))),
            package=(None, "report-1", None, None, "report_only"),
            configuration=configuration,
            scope="books",
        )
        == ()
    )


@pytest.mark.parametrize(
    "shape,expected",
    (
        ("credential", "authentication"),
        ("absent", "terminal"),
        ("ordinary", "terminal"),
        ("intervening", "terminal"),
        ("raw_carrier", "transient"),
    ),
)
def test_declared_failure_unwraps_only_one_credential_cause(shape, expected):
    carrier = ProviderObservationFailure(ProviderFailureSnapshot())
    if shape != "absent":
        carrier.__cause__ = (
            RuntimeError("ordinary")
            if shape == "ordinary"
            else CredentialError("credential")
        )
    cause: Exception = carrier
    if shape == "intervening":
        cause = RuntimeError("intervening")
        cause.__cause__ = carrier
    error = ProviderRetrySuccessorError("provider retry dispatch failed")
    error.__cause__ = cause
    assert classify_provider_recovery_failure(
        carrier if shape == "raw_carrier" else error
    ) == (expected, None)


@pytest.mark.parametrize("count", [0, 1, 54])
def test_observed_accounting_preserves_known_zero_and_bound(count: int) -> None:
    from mechanical_librarian.application.provider_recovery_evidence import (
        observed_recovery_evidence,
    )
    from mechanical_librarian.domain.provider_recovery import RecoveryAttemptEvidence

    observation = replace(
        _provider("catalogue", "candidate", ()),
        request_accounting=ProviderRequestAccounting(count, count),
    )
    assert observed_recovery_evidence(observation) == RecoveryAttemptEvidence(
        "terminal_failure", count, "observed"
    )


@pytest.mark.parametrize("records", [None, [], (object(),)])
def test_partial_accounting_rejects_untyped_records(records: object) -> None:
    from mechanical_librarian.application.provider_evidence import (
        ProviderOperationalEvidence,
    )
    from mechanical_librarian.application.provider_recovery_evidence import (
        partial_recovery_evidence,
    )
    from mechanical_librarian.domain.provider_recovery import RecoveryAttemptEvidence

    snapshot = ProviderFailureSnapshot(
        evidence=ProviderOperationalEvidence(attempt_records=cast(Any, records))
    )
    assert partial_recovery_evidence(
        ProviderObservationFailure(snapshot), "catalogue"
    ) == RecoveryAttemptEvidence("terminal_failure")


@pytest.mark.parametrize("count", [0, 1, 54, 55])
def test_partial_accounting_is_bounded_completed_record_lower_bound(count: int) -> None:
    from mechanical_librarian.application.provider_evidence import (
        ProviderAttemptRecord,
        ProviderOperationalEvidence,
    )
    from mechanical_librarian.application.provider_recovery_evidence import (
        partial_recovery_evidence,
    )
    from mechanical_librarian.domain.provider_recovery import RecoveryAttemptEvidence

    records = tuple(
        ProviderAttemptRecord("catalogue", ordinal, "http_429", None, 0.1, "terminal")
        for ordinal in range(1, count + 1)
    )
    error = ProviderObservationFailure(
        ProviderFailureSnapshot(
            evidence=ProviderOperationalEvidence(attempt_records=records)
        )
    )
    result = partial_recovery_evidence(error, "catalogue")
    assert result == (
        RecoveryAttemptEvidence("terminal_failure", count, "partial")
        if count <= 54
        else RecoveryAttemptEvidence("terminal_failure")
    )


@pytest.mark.parametrize(
    "accounting", [None, object(), ProviderRequestAccounting(55, 55)]
)
def test_observed_accounting_missing_or_oversized_is_unknown(
    accounting: object,
) -> None:
    from mechanical_librarian.application.provider_recovery_evidence import (
        observed_recovery_evidence,
    )
    from mechanical_librarian.domain.provider_recovery import RecoveryAttemptEvidence

    observation = _provider("catalogue", "candidate", ())
    object.__setattr__(observation, "request_accounting", accounting)
    assert observed_recovery_evidence(observation) == RecoveryAttemptEvidence(
        "terminal_failure"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_dispatches", True),
        ("retry_dispatches", -1),
        ("logical_requests", 4),
        ("replays", 0.0),
    ],
)
def test_observed_accounting_revalidates_exact_numbers(
    field: str, value: object
) -> None:
    from mechanical_librarian.application.provider_recovery_evidence import (
        observed_recovery_evidence,
    )
    from mechanical_librarian.domain.provider_recovery import RecoveryAttemptEvidence

    accounting = ProviderRequestAccounting(1, 1)
    object.__setattr__(accounting, field, value)
    observation = _provider("catalogue", "candidate", ())
    object.__setattr__(observation, "request_accounting", accounting)
    assert observed_recovery_evidence(observation) == RecoveryAttemptEvidence(
        "terminal_failure"
    )


@pytest.mark.parametrize(
    "provider,ordinal",
    [
        ("other", 1),
        ("catalogue", True),
        ("catalogue", 2),
        ("catalogue", 0),
        ("catalogue", 1.0),
    ],
)
def test_partial_accounting_requires_exact_provider_and_contiguous_ordinals(
    provider: str, ordinal: object
) -> None:
    from mechanical_librarian.application.provider_evidence import (
        ProviderAttemptRecord,
        ProviderOperationalEvidence,
    )
    from mechanical_librarian.application.provider_recovery_evidence import (
        partial_recovery_evidence,
    )
    from mechanical_librarian.domain.provider_recovery import RecoveryAttemptEvidence

    record = ProviderAttemptRecord(
        provider, cast(int, ordinal), "http_429", None, 0.1, "terminal"
    )
    error = ProviderObservationFailure(
        ProviderFailureSnapshot(
            evidence=ProviderOperationalEvidence(attempt_records=(record,))
        )
    )
    assert partial_recovery_evidence(error, "catalogue") == RecoveryAttemptEvidence(
        "terminal_failure"
    )
