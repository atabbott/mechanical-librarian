from collections.abc import Callable

import pytest

from mechanical_librarian.application.proposal_sets import (
    CreateProposalSet,
    GetProposalSet,
    ProposalCurrentnessError,
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
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report


def _report() -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")),
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

    def get(self, report_id: str) -> str:
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

    serialized = CreateProposalSet(repository, current).run(
        report.report_id
    )
    proposal_set = evaluate_report(report)
    assert proposal_set.to_json() == serialized
    assert ["get_report", "current", "publish", "get_proposal"] == repository.events
    assert serialized == GetProposalSet(repository).run(proposal_set.proposal_set_id)


def test_stale_report_publishes_nothing() -> None:
    report = _report()
    repository = _Repository(report)
    with pytest.raises(ProposalSetError):
        CreateProposalSet(repository, lambda _: False).run(report.report_id)
    assert "publish" not in repository.events
    assert {} == repository.proposals


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
            "proposal currency check failed",
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

    Policy resolution, evaluation and the currency check all ran inside the
    block whose last resort said `proposal publication failed`, so an
    injected resolver or a currency seam that raised reported a publication
    that was never attempted -- and the repository proves it was not, since
    nothing reached `publish` (#698).
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
