from collections.abc import Callable

import pytest

from mechanical_librarian.application.metadata_report import ReportNotFoundError
from mechanical_librarian.application.proposal_sets import (
    CreateProposalSet,
    GetProposalSet,
    ProposalCurrentnessError,
    ProposalEvaluationError,
    ProposalInternalError,
    ProposalMissingInputError,
    ProposalPolicyError,
    ProposalPublicationError,
    ProposalRetainedDataError,
    ProposalSetError,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalValueError,
    evaluate_report,
)


def _report() -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )


def _raise(error: BaseException):
    """A seam that fails, so a phase can be made to fail one at a time."""

    def seam(*_arguments: object, **_keywords: object):
        raise error

    return seam


class _Repository:
    def __init__(self, report: MetadataReport) -> None:
        self.report = report
        self.proposals: dict[str, str] = {}
        self.events: list[str] = []

    def get_proposal_input(self, report_id: str) -> str:
        self.events.append("get_report")
        if report_id != self.report.report_id:
            raise ValueError
        return self.report.to_json()

    def publish_proposal(self, proposal_set) -> str:
        self.events.append("publish")
        serialized = proposal_set.to_json()
        self.proposals[proposal_set.proposal_set_id] = serialized
        return serialized

    def get_proposal(self, proposal_set_id: str) -> str:
        self.events.append("get_proposal")
        return self.proposals[proposal_set_id]

    def close(self) -> None:
        pass


def test_currentness_precedes_publication_and_readback_is_exact() -> None:
    report = _report()
    repository = _Repository(report)

    def current(value: MetadataReport) -> bool:
        assert report == value
        repository.events.append("current")
        return True

    serialized = CreateProposalSet(repository, current).run(report.report_id)
    proposal_set = evaluate_report(report)
    assert proposal_set.to_json() == serialized
    assert ["get_report", "current", "publish", "get_proposal"] == repository.events
    assert serialized == GetProposalSet(repository).run(proposal_set.proposal_set_id)


def test_stale_report_publishes_nothing() -> None:
    report = _report()
    repository = _Repository(report)
    with pytest.raises(ProposalCurrentnessError):
        CreateProposalSet(repository, lambda _: False).run(report.report_id)
    assert "publish" not in repository.events
    assert {} == repository.proposals


@pytest.mark.parametrize(
    ("configure", "expected"),
    (
        (
            lambda repository: setattr(
                repository,
                "get_proposal_input",
                _raise(ReportNotFoundError("report")),
            ),
            ProposalMissingInputError,
        ),
        (
            lambda repository: setattr(
                repository, "get_proposal_input", _raise(RuntimeError("store fault"))
            ),
            ProposalInternalError,
        ),
    ),
    ids=("missing-report", "read-fault"),
)
def test_report_reopen_uses_closed_failure_taxonomy(
    configure: Callable[[_Repository], None], expected: type[ProposalSetError]
) -> None:
    report = _report()
    repository = _Repository(report)
    configure(repository)
    with pytest.raises(expected):
        CreateProposalSet(repository, lambda _: True).run(report.report_id)
    assert "publish" not in repository.events


def test_malformed_report_is_retained_data() -> None:
    report = _report()
    repository = _Repository(report)
    repository.get_proposal_input = lambda _report_id: "{}"  # type: ignore[assignment, method-assign]  # reason: this test fixes malformed stored report bytes at the repository boundary

    with pytest.raises(ProposalRetainedDataError):
        CreateProposalSet(repository, lambda _: True).run(report.report_id)
    assert "publish" not in repository.events


def test_report_identity_disagreement_is_retained_data() -> None:
    report = _report()
    repository = _Repository(report)
    requested_id = "report_" + "c" * 64
    repository.get_proposal_input = lambda _report_id: report.to_json()  # type: ignore[assignment]  # reason: this test fixes the stored report independently of the requested identity

    with pytest.raises(ProposalRetainedDataError):
        CreateProposalSet(repository, lambda _: True).run(requested_id)
    assert "publish" not in repository.events


@pytest.mark.parametrize(
    ("service", "expected"),
    (
        (
            lambda repository: CreateProposalSet(
                repository,
                lambda _: True,
                resolve_policy_profile=_raise(RuntimeError("policy fault")),
            ),
            ProposalPolicyError,
        ),
        (
            lambda repository: CreateProposalSet(
                repository, _raise(RuntimeError("currentness fault"))
            ),
            ProposalInternalError,
        ),
        (
            lambda repository: CreateProposalSet(repository, lambda _: True),
            ProposalPublicationError,
        ),
    ),
    ids=("policy", "currentness", "publication"),
)
def test_phase_faults_have_closed_types(
    service: Callable[[_Repository], CreateProposalSet],
    expected: type[ProposalSetError],
) -> None:
    report = _report()
    repository = _Repository(report)
    if expected is ProposalPublicationError:
        repository.publish_proposal = _raise(RuntimeError("publication fault"))  # type: ignore[method-assign]  # reason: this test makes the publication port fail without changing earlier phases

    with pytest.raises(expected):
        service(repository).run(report.report_id)
    assert "publish" not in repository.events or expected is ProposalPublicationError


def test_evaluation_fault_is_closed_and_does_not_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report()
    repository = _Repository(report)
    monkeypatch.setattr(
        "mechanical_librarian.application.proposal_sets.evaluate_report",
        _raise(ProposalValueError("evaluation fault")),
    )

    with pytest.raises(ProposalEvaluationError):
        CreateProposalSet(repository, lambda _: True).run(report.report_id)
    assert "publish" not in repository.events


def test_noncanonical_publication_readback_is_retained_data() -> None:
    report = _report()
    repository = _Repository(report)

    def noncanonical(proposal_set) -> str:
        repository.events.append("publish")
        serialized = proposal_set.to_json() + " "
        repository.proposals[proposal_set.proposal_set_id] = serialized
        return serialized

    repository.publish_proposal = noncanonical  # type: ignore[method-assign]  # reason: this test supplies a deliberately noncanonical retained document
    with pytest.raises(ProposalRetainedDataError):
        CreateProposalSet(repository, lambda _: True).run(report.report_id)


def test_get_proposal_distinguishes_missing_and_malformed_retained_data() -> None:
    report = _report()
    repository = _Repository(report)
    proposal_set_id = "proposal_set_" + "a" * 64

    with pytest.raises(ProposalMissingInputError):
        GetProposalSet(repository).run(proposal_set_id)
    repository.proposals[proposal_set_id] = "{}"
    with pytest.raises(ProposalRetainedDataError):
        GetProposalSet(repository).run(proposal_set_id)


def test_typed_currentness_failure_is_not_wrapped_as_publication_failure() -> None:
    report = _report()
    repository = _Repository(report)

    def stale(_: MetadataReport) -> bool:
        raise ProposalCurrentnessError("proposal report is stale")

    with pytest.raises(ProposalCurrentnessError):
        CreateProposalSet(repository, stale).run(report.report_id)
    assert "publish" not in repository.events


@pytest.mark.parametrize(
    ("message", "service"),
    (
        (
            "proposal policy resolution failed",
            lambda repository, fault: CreateProposalSet(
                repository,
                lambda _: True,
                resolve_policy_profile=_raise(fault),
            ),
        ),
        (
            "proposal currentness check failed",
            lambda repository, fault: CreateProposalSet(repository, _raise(fault)),
        ),
    ),
    ids=("policy", "currency"),
)
def test_each_phase_before_publication_reports_itself_not_publication(
    message: str,
    service: Callable[[_Repository, BaseException], CreateProposalSet],
) -> None:
    """A phase that fails before publication no longer claims publication.

    An error in policy resolution or the currentness seam stops before the
    repository receives a publication request (#698).
    """
    report = _report()
    fault = RuntimeError("seam is broken")

    repository = _Repository(report)
    with pytest.raises(ProposalSetError) as raised:
        service(repository, fault).run(report.report_id)
    assert str(raised.value) == message
    assert raised.value.__cause__ is fault
    assert "publish" not in repository.events
    assert {} == repository.proposals

    # The publication pair keeps the identity it always had, and is now the
    # only thing the last resort can be reporting.
    repository = _Repository(report)
    repository.publish_proposal = _raise(fault)  # type: ignore[method-assign]
    with pytest.raises(ProposalSetError) as raised:
        CreateProposalSet(repository, lambda _: True).run(report.report_id)
    assert str(raised.value) == "proposal publication failed"
    assert raised.value.__cause__ is fault


@pytest.mark.parametrize(
    "state",
    (
        TargetFieldState.NULL,
        TargetFieldState.EMPTY,
        TargetFieldState.UNLOADED,
        TargetFieldState.MALFORMED,
        TargetFieldState.OVERSIZED,
    ),
    ids=("null", "empty", "unloaded", "malformed", "oversized"),
)
def test_nonvalue_title_states_create_without_current_title_failure(
    state: TargetFieldState,
) -> None:
    fields = tuple(
        TargetField(TargetDestination.TITLE, state)
        if field.destination is TargetDestination.TITLE
        else field
        for field in default_target_fields("Title")
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    repository = _Repository(report)
    serialized = CreateProposalSet(
        repository,
        lambda value: value.target.current_title is None,
    ).run(report.report_id)
    proposal_set = evaluate_report(report)
    title = next(
        result
        for result in proposal_set.results
        if result.destination is TargetDestination.TITLE
    )
    assert proposal_set.to_json() == serialized
    assert ProposalOutcome.INELIGIBLE == title.outcome
    assert title.selected_alternative_id is None
