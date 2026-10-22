"""Shared builders for automatic decisions persisted through public seams."""

from __future__ import annotations

import json
from typing import Protocol

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet


class _AutomaticDecisionEnvironment(Protocol):
    @property
    def repository(self) -> SQLiteMetadataReportRepository: ...


def _automatic_decisions(
    environment: _AutomaticDecisionEnvironment,
    report: MetadataReport,
    category: str,
    option: str,
    *,
    qualified: bool,
    also: tuple[tuple[str, str], ...] = (),
) -> tuple[AutomaticDecision, ...]:
    """Persist naturally supported automatic outcomes through public seams.

    ``also`` makes further categories automatic in the same preparation.
    """
    options = dict(DEFAULT_OPTIONS)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    for name, value in ((category, option), *also):
        options[name] = value
        destinations[name] = "automatic"
    profile = MetadataPolicyProfile.create("Activity automatic", options, destinations)
    resolved = resolve_policy(profile, report.target.library)
    environment.repository.enter_shadow_preview(1)
    environment.repository.create_metadata_policy_draft(profile.canonical_json)
    environment.repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    if qualified:
        environment.repository.activate_qualified_automatic(3)
    proposal_set = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    environment.repository.publish(report, lambda: True)
    environment.repository.publish_proposal(proposal_set)
    case_id = json.loads(environment.repository.admit_review_case(proposal_set))[
        "review_case_id"
    ]
    assert type(case_id) is str
    ShadowOutcomeSet.from_json(
        environment.repository.persist_shadow_outcome(case_id, 4)
    )
    return tuple(
        AutomaticDecision.from_json(value)
        for value in environment.repository.get_review_case_decisions(case_id)
    )
