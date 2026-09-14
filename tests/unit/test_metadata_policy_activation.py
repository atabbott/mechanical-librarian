from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.persistence import field_effect_activity
from mechanical_librarian.adapters.persistence.decisions import (
    _REPLACEMENT_BLOCKING_EFFECT_SQL,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.metadata_policy import (
    MetadataPolicyApplicationError,
    PolicyContentionError,
    PolicyImpactRepository,
    PolicyNotebook,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.automatic_decision import QUALIFIED_POLICY_OPTIONS
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
    field_value_version,
)
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    QUALIFIED_AUTOMATIC,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from tests.unit.test_review_pipeline import _PRODUCER, _entry, _report


class _CaseWithTargetFields:
    """Decode review cases whose target projects a malformed `fields` value.

    `ReviewCase` validates and canonicalises its own document, so a target
    this shape cannot be persisted. Substituting the decoder the repository
    calls reaches the projection reader without weakening that validation.
    """

    def __init__(self, fields: object) -> None:
        self._fields = fields

    def from_json(self, serialized: str) -> object:
        case = ReviewCase.from_json(serialized)
        fields = self._fields

        class _Case:
            def __getattr__(self, name: str) -> object:
                return getattr(case, name)

            @property
            def target(self) -> dict[str, object]:
                return {**case.target, "fields": fields}

        return _Case()


def test_unrelated_verified_cover_effect_does_not_block_another_decision() -> None:
    """The replacement predicate binds every state branch to its decision."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE field_effects(decision_id TEXT,state TEXT)")
    connection.execute(
        "INSERT INTO field_effects VALUES('cover-decision-on-other-case','verified')"
    )
    query = (
        "SELECT 1 FROM field_effects WHERE decision_id=? AND "
        + _REPLACEMENT_BLOCKING_EFFECT_SQL
        + " LIMIT 1"
    )
    assert connection.execute(query, ("scalar-decision", True)).fetchone() is None
    connection.execute("INSERT INTO field_effects VALUES('cover-decision','verified')")
    assert connection.execute(query, ("cover-decision", True)).fetchone() == (1,)
    connection.close()


def _draft() -> MetadataPolicyProfile:
    options = dict(DEFAULT_OPTIONS)
    options["date_precision"] = "full_date"
    return MetadataPolicyProfile.create(
        "Reviewed policy",
        options,
        {
            field: "ignore"
            if field == "tags"
            else ("unsupported" if field == "explicit" else "ask")
            for field in OPTION_CATALOGUE
        },
    )


@pytest.fixture
def state_directory() -> Iterator[Path]:
    """A temporary directory holding this test's sqlite state files."""

    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


@pytest.fixture
def state_path(state_directory: Path) -> Path:
    return state_directory / "state.sqlite"


def _current_case_with_completed_work(path: Path, report: MetadataReport | None = None):
    repository = open_repository(path)
    report = _report() if report is None else report
    repository.publish(report, lambda: True)
    old = evaluate_report(report)
    repository.publish_proposal(old)
    admitted = json.loads(repository.admit_review_case(old))
    repository.request_review_scan(
        report.target.connection, report.target.library, "lib-1", _PRODUCER, 10
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        (
            replace(
                _entry(report.target.item, report.file.identity),
                library=report.target.library,
            ),
        ),
        12,
    )
    action = repository.claim_review_pipeline_action(13)
    assert action is not None
    repository.advance_review_pipeline_action(action, report.report_id, 14)
    action = repository.claim_review_pipeline_action(15)
    assert action is not None
    repository.advance_review_pipeline_action(action, None, 16)
    action = repository.claim_review_pipeline_action(17)
    assert action is not None
    repository.advance_review_pipeline_action(action, old.proposal_set_id, 18)
    action = repository.claim_review_pipeline_action(19)
    assert action is not None
    repository.advance_review_pipeline_action(action, admitted["review_case_id"], 20)
    action = repository.claim_review_pipeline_action(21)
    assert action is not None
    repository.advance_review_pipeline_action(action, None, 22)
    repository.claim_review_pipeline_action(23)
    return repository, report, old, admitted["review_case_id"]


def _human_decision(repository, case_id: str, idempotency_key: str) -> HumanDecision:
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    return HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key=idempotency_key,
        occurred_at=24,
    )


def test_qualified_automatic_intent_is_persistable_and_unqualified_intent_is_rejected(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["title_layout"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Preview automatic intent", dict(DEFAULT_OPTIONS), destinations
    )
    assert profile.policy_id == repository.create_metadata_policy_draft(
        profile.canonical_json
    )
    unqualified_options = dict(DEFAULT_OPTIONS)
    unqualified_options["publisher_preference"] = "imprint"
    unqualified = MetadataPolicyProfile.create(
        "Unqualified automatic intent",
        unqualified_options,
        {**destinations, "publisher_preference": "automatic"},
    )
    with pytest.raises(MetadataReportError, match="automatic intent is unqualified"):
        repository.create_metadata_policy_draft(unqualified.canonical_json)

    rich_automatic = MetadataPolicyProfile.create(
        "Rich automatic intent",
        dict(DEFAULT_OPTIONS),
        {**destinations, "genres": "automatic"},
    )
    notebook = PolicyNotebook(repository, (), {})
    assert rich_automatic == notebook.validate(rich_automatic)
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    resolved = resolve_policy(profile, "ebooks")
    transition = repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        (("ebooks", resolved.resolved_id, resolved.canonical_json),),
        2,
    )
    assert transition is not None
    assert "completed" == transition.state
    assert "preview" == repository.shadow_posture()
    assert (
        "automatic"
        == dict(
            cast(
                ResolvedMetadataPolicy,
                repository.current_resolved_metadata_policy("ebooks"),
            ).destinations
        )["title_layout"]
    )
    assert "title_layout" in QUALIFIED_AUTOMATIC
    repository.close()


def test_automatic_posture_accepts_only_exact_qualified_resolutions(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["title_layout"] = "automatic"
    first = MetadataPolicyProfile.create(
        "First qualified automatic policy", dict(DEFAULT_OPTIONS), destinations
    )
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(first.canonical_json)
    first_resolved = resolve_policy(first, "ebooks")
    first_transition = repository.activate_metadata_policy(
        first.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        (("ebooks", first_resolved.resolved_id, first_resolved.canonical_json),),
        2,
    )
    assert first_transition is not None
    assert "automatic" == repository.activate_qualified_automatic(3)

    second = MetadataPolicyProfile.create(
        "Second qualified automatic policy",
        {**dict(DEFAULT_OPTIONS), "date_precision": "full_date"},
        {**destinations, "date_precision": "automatic"},
    )
    repository.create_metadata_policy_draft(second.canonical_json)
    second_resolved = resolve_policy(second, "ebooks")
    transition = repository.activate_metadata_policy(
        second.policy_id,
        first.policy_id,
        (("ebooks", second_resolved.resolved_id, second_resolved.canonical_json),),
        4,
    )
    assert transition is not None
    assert transition == repository.activate_metadata_policy(
        second.policy_id,
        first.policy_id,
        (("ebooks", second_resolved.resolved_id, second_resolved.canonical_json),),
        4,
    )
    with pytest.raises(MetadataReportError, match="stale"):
        repository.activate_metadata_policy(
            first.policy_id,
            first.policy_id,
            (("ebooks", first_resolved.resolved_id, first_resolved.canonical_json),),
            5,
        )

    unqualified = resolve_policy(
        second,
        "ebooks",
        {
            "options": {"publisher_preference": "imprint"},
            "destinations": {"publisher_preference": "automatic"},
        },
    )
    with pytest.raises(MetadataReportError, match="resolution is invalid"):
        repository.activate_metadata_policy(
            second.policy_id,
            second.policy_id,
            (("ebooks", unqualified.resolved_id, unqualified.canonical_json),),
            6,
            reconcile_current=True,
        )
    repository.close()


def test_confirm_automatic_keeps_the_read_back_refusal_distinct() -> None:
    """An activation that succeeded is not reported as one that failed.

    `confirm_automatic` activates durably and then returns `shadow_state()`,
    which raises `shadow posture is unavailable` of its own. The broad
    handler caught that refusal and replaced it with `automatic posture is
    unavailable`, so after the durable write a caller could not tell an
    activation that never happened from one that happened and whose
    read-back did not. `enter_preview` beside it already re-raised (#698).
    """
    activations: list[int] = []

    class ReadBackFails:
        def activate_qualified_automatic(self, updated_at: int) -> str:
            activations.append(updated_at)
            return "automatic"

        def shadow_posture(self) -> str:
            raise RuntimeError("posture read failed")

    notebook = PolicyNotebook(cast("PolicyImpactRepository", ReadBackFails()), (), {})
    with pytest.raises(MetadataPolicyApplicationError) as raised:
        notebook.confirm_automatic(2)
    assert str(raised.value) == "shadow posture is unavailable"
    assert activations == [2]

    class ActivationFails:
        def activate_qualified_automatic(self, updated_at: int) -> str:
            raise RuntimeError("activation did not commit")

        def shadow_posture(self) -> str:
            return "automatic"

    # The activation lane keeps its own identity unchanged.
    notebook = PolicyNotebook(cast("PolicyImpactRepository", ActivationFails()), (), {})
    with pytest.raises(MetadataPolicyApplicationError) as raised:
        notebook.confirm_automatic(2)
    assert str(raised.value) == "automatic posture is unavailable"


def test_policy_contention_is_read_from_the_condition_not_the_message(
    state_path: Path,
) -> None:
    """Control flow follows the seam's typed condition, not its wording.

    `activate` and `retry` recognised contention with `"stale" in
    str(error)`, so rewording an adapter message silently changed which
    branch ran, and any unrelated failure whose message happened to contain
    the word was reported to the owner as staleness -- a 409 telling them to
    reload, for a fault reloading cannot fix. The condition travels on the
    exception now, and the messages below deliberately carry none of the
    words the old tests would have matched (#698).
    """
    repository = open_repository(state_path)
    profile = MetadataPolicyProfile.create(
        "Contention profile",
        dict(DEFAULT_OPTIONS),
        {
            category: "unsupported" if category == "explicit" else "ask"
            for category in OPTION_CATALOGUE
        },
    )
    repository.create_metadata_policy_draft(profile.canonical_json)
    notebook = PolicyNotebook(repository, (), {})
    builtin = MetadataPolicyProfile.builtin().policy_id

    def activation(failure: BaseException) -> str:
        with (
            patch.object(
                type(repository), "activate_metadata_policy", side_effect=failure
            ),
            pytest.raises(MetadataPolicyApplicationError) as raised,
        ):
            notebook.activate(profile.policy_id, builtin, 2)
        return str(raised.value)

    # Each condition routes on its own, with a message carrying none of the
    # words the branch used to search for.
    for condition, message in (
        ("stale", "policy activation is stale"),
        ("processing", "policy transition is processing"),
        ("unqualified", "policy activation is unqualified"),
    ):
        assert activation(PolicyContentionError(condition, "state moved")) == message

    # The regression: an unrelated failure is no longer misread as
    # contention because of a word in its message.
    assert (
        activation(MetadataReportError("the stale cache could not be read"))
        == "policy activation is unavailable"
    )

    def retry(failure: BaseException) -> str:
        with (
            patch.object(
                type(repository),
                "retry_metadata_policy_transition",
                side_effect=failure,
            ),
            pytest.raises(MetadataPolicyApplicationError) as raised,
        ):
            notebook.retry("policy_transition_" + "a" * 64, 3)
        return str(raised.value)

    assert (
        retry(PolicyContentionError("stale", "state moved"))
        == "policy transition is stale"
    )
    assert (
        retry(MetadataReportError("the stale cache could not be read"))
        == "policy transition is unavailable"
    )

    # The condition vocabulary is closed, so a seam cannot invent one.
    with pytest.raises(MetadataReportError):
        PolicyContentionError("wedged", "state moved")
    repository.close()


def test_asin_automatic_routing_requires_the_current_audiobook_record(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Current audiobook routing requires its measured record; ebooks stay Ask."""
    repository = open_repository(state_path)
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["asin"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "ASIN automatic intent", dict(DEFAULT_OPTIONS), destinations
    )
    notebook = PolicyNotebook(repository, ("audio",), {})

    assert "asin" in QUALIFIED_AUTOMATIC
    with monkeypatch.context() as withdrawn:
        withdrawn.setattr(
            audiobook_decision_qualification,
            "F46_PASSING_AUTOMATIC_OPTIONS",
            audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
            - {audiobook_decision_qualification.F46_ASIN_IDENTITY},
        )
        with pytest.raises(
            MetadataReportError, match="automatic intent is unqualified"
        ):
            repository.create_metadata_policy_draft(profile.canonical_json)
        with pytest.raises(
            MetadataPolicyApplicationError, match="automatic unqualified"
        ):
            notebook.validate(profile)

    assert profile == notebook.validate(profile)
    assert profile.policy_id == repository.create_metadata_policy_draft(
        profile.canonical_json
    )
    repository.enter_shadow_preview(1)
    resolved = resolve_policy(profile, "audio")
    transition = repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        (("audio", resolved.resolved_id, resolved.canonical_json),),
        2,
    )
    assert transition is not None and "completed" == transition.state
    # The record widened routing, not the ebook option catalogue.
    assert "asin:preserve_exact" not in QUALIFIED_POLICY_OPTIONS
    assert ("preserve_exact", "ebook_manifestation") == OPTION_CATALOGUE["asin"]
    repository.close()


def test_library_override_cannot_turn_an_unqualified_option_automatic(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["publisher_preference"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Qualified base option", dict(DEFAULT_OPTIONS), destinations
    )
    repository.create_metadata_policy_draft(profile.canonical_json)
    notebook = PolicyNotebook(
        repository,
        ("ebooks",),
        {
            "ebooks": {
                "options": {"publisher_preference": "imprint"},
                "destinations": {},
            }
        },
    )
    with pytest.raises(MetadataPolicyApplicationError, match="automatic unqualified"):
        notebook.activate(
            profile.policy_id, MetadataPolicyProfile.builtin().policy_id, 2
        )
    repository.close()


def test_initial_resolution_binding_rejects_an_unqualified_automatic_override(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    resolved = resolve_policy(
        MetadataPolicyProfile.builtin(),
        "ebooks",
        {"options": {"publisher_preference": "imprint"}, "destinations": {}},
    )
    with pytest.raises(MetadataReportError, match="unqualified"):
        repository.bind_initial_metadata_policy_resolution(resolved.canonical_json)
    assert (0,) == repository._connection.execute(
        "SELECT COUNT(*) FROM metadata_policy_resolutions"
    ).fetchone()
    repository.close()


def test_initial_resolution_binding_joins_an_existing_transaction(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    resolved = resolve_policy(MetadataPolicyProfile.builtin(), "ebooks")
    repository._connection.execute("BEGIN IMMEDIATE")

    assert resolved.resolved_id == repository.bind_initial_metadata_policy_resolution(
        resolved.canonical_json
    )
    assert repository._connection.in_transaction
    assert (resolved.resolved_id,) == repository._connection.execute(
        "SELECT resolved_policy_id FROM metadata_policy_resolutions "
        "WHERE generation=1 AND library_name='ebooks'"
    ).fetchone()

    repository._connection.rollback()
    assert (0,) == repository._connection.execute(
        "SELECT COUNT(*) FROM metadata_policy_resolutions "
        "WHERE generation=1 AND library_name='ebooks'"
    ).fetchone()
    repository.close()


def test_bootstrap_generation_is_a_baseline_and_owner_activation_is_not(
    state_path: Path,
) -> None:
    """A fresh container has activated nothing, and the projection says so.

    `_create_schema` seeds one completed generation-1 transition so the
    built-in policy has an activation history. Read without a discriminator
    that row is indistinguishable from an owner activation over zero works,
    which is exactly what the interface reported.
    """

    repository, report, _, _ = _current_case_with_completed_work(state_path)
    seeded = repository.metadata_policy_transition()
    assert seeded is not None
    assert (seeded.origin, seeded.generation, seeded.total) == ("baseline", 1, 0)

    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)
    activated = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        30,
    )
    assert activated is not None
    assert (activated.origin, activated.generation) == ("activation", 2)
    assert repository.metadata_policy_transition() == activated
    repository.close()


def test_atomic_cutover_rewinds_existing_evidence_and_completes_after_restart(
    state_path: Path,
) -> None:
    repository, report, old, old_case_id = _current_case_with_completed_work(state_path)
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    builtin = MetadataPolicyProfile.builtin()
    resolved = resolve_policy(draft, report.target.library)

    transition = repository.activate_metadata_policy(
        draft.policy_id,
        builtin.policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        30,
    )
    assert transition is not None
    assert (
        transition.state,
        transition.completed,
        transition.total,
        transition.failed,
    ) == ("processing", 0, 1, 0)
    assert transition == repository.activate_metadata_policy(
        draft.policy_id,
        builtin.policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        30,
    )
    follow_up = MetadataPolicyProfile.create(
        "Follow-up policy",
        {**dict(draft.options), "language_rendering": "preserve"},
        dict(draft.destinations),
    )
    repository.create_metadata_policy_draft(follow_up.canonical_json)
    follow_up_resolved = resolve_policy(follow_up, report.target.library)
    with pytest.raises(MetadataReportError, match="processing"):
        repository.activate_metadata_policy(
            follow_up.policy_id,
            draft.policy_id,
            (
                (
                    report.target.library,
                    follow_up_resolved.resolved_id,
                    follow_up_resolved.canonical_json,
                ),
            ),
            30,
        )
    assert repository.current_unapplied_count(report.target.library) == 1
    assert repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone() == (None, "retired")
    assert repository._connection.execute(
        "SELECT actor,kind,reason FROM review_case_transitions ORDER BY checkpoint DESC LIMIT 1"
    ).fetchone() == ("policy-activation-v1", "retired", "policy_changed")
    assert json.loads(repository.get_review_case(old_case_id))["current"]
    with pytest.raises(MetadataReportError):
        repository.publish_proposal(old)

    new_report = MetadataReport.create(
        target=replace(report.target, item="item-new"),
        file=replace(report.file, identity="d" * 64, content_digest="e" * 64),
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        cover_sources=report.cover_sources,
    )
    repository.publish(new_report, lambda: True)
    active_resolution = repository.current_resolved_metadata_policy(
        new_report.target.library
    )
    assert active_resolution is not None
    new_proposal = evaluate_report(
        new_report,
        active_resolution.resolved_id,
        dict(active_resolution.destinations),
    )
    repository.publish_proposal(new_proposal)
    repository.admit_review_case(new_proposal)
    unchanged = repository.metadata_policy_transition()
    assert unchanged is not None
    assert (unchanged.completed, unchanged.total) == (0, 1)
    repository.close()

    reopened = open_repository(state_path, create=False)
    with pytest.raises(MetadataReportError, match="processing"):
        reopened.activate_metadata_policy(
            follow_up.policy_id,
            draft.policy_id,
            (
                (
                    report.target.library,
                    follow_up_resolved.resolved_id,
                    follow_up_resolved.canonical_json,
                ),
            ),
            30,
        )
    action = reopened.claim_review_pipeline_action(31)
    assert action is not None
    assert (action.stage, action.report_id) == ("proposal", report.report_id)
    current_policy = reopened.current_resolved_metadata_policy(report.target.library)
    assert current_policy is not None
    successor = evaluate_report(
        report, current_policy.resolved_id, dict(current_policy.destinations)
    )
    tags = next(
        result
        for result in successor.results
        if result.destination is TargetDestination.TAGS
    )
    assert (tags.outcome, tags.reasons) == (
        ProposalOutcome.INELIGIBLE,
        ("policy_ignored",),
    )
    reopened.publish_proposal(successor)
    reopened.advance_review_pipeline_action(action, successor.proposal_set_id, 32)
    action = reopened.claim_review_pipeline_action(33)
    assert action is not None
    admitted = json.loads(reopened.admit_review_case(successor))
    reopened.advance_review_pipeline_action(action, admitted.get("review_case_id"), 34)
    action = reopened.claim_review_pipeline_action(35)
    assert action is not None
    reopened.advance_review_pipeline_action(action, None, 36)
    completed = reopened.metadata_policy_transition()
    assert completed is not None
    assert (
        completed.state,
        completed.completed,
        completed.total,
        completed.failed,
    ) == ("completed", 1, 1, 0)
    assert admitted.get("review_case_id") != old_case_id
    successor_case_id = admitted.get("review_case_id")
    assert type(successor_case_id) is str
    assert json.loads(reopened.get_review_case(successor_case_id))["current"]
    browsed = reopened.list_current_review_case_documents(None, 50, 0, None)
    assert successor_case_id in tuple(
        ReviewCase.from_json(value[0]).review_case_id for value in browsed
    )
    packet = reopened.preview_shadow_packet(successor_case_id)
    assert packet.review_case_id == successor_case_id
    acknowledged = reopened.acknowledge_metadata_policy_transition(
        completed.transition_id, 37
    )
    assert acknowledged.acknowledged
    assert (
        reopened.activate_metadata_policy(
            draft.policy_id,
            draft.policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            38,
        )
        is None
    )
    next_transition = reopened.activate_metadata_policy(
        follow_up.policy_id,
        draft.policy_id,
        (
            (
                report.target.library,
                follow_up_resolved.resolved_id,
                follow_up_resolved.canonical_json,
            ),
        ),
        39,
    )
    assert next_transition is not None
    assert 3 == next_transition.generation
    reopened.close()


def test_missing_pipeline_work_is_typed_durable_and_retryable(state_path: Path) -> None:
    repository = open_repository(state_path)
    report = _report()
    repository.publish(report, lambda: True)
    old = evaluate_report(report)
    repository.publish_proposal(old)
    repository.admit_review_case(old)
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)
    transition = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        40,
    )
    assert transition is not None
    assert (transition.state, transition.failed, transition.total) == (
        "completed_with_failures",
        1,
        1,
    )
    assert repository._connection.execute(
        "SELECT state,failure_code FROM metadata_policy_transition_items"
    ).fetchone() == ("failed", "pipeline_unavailable")
    blocked = MetadataPolicyProfile.create(
        "Blocked follow-up",
        {**dict(draft.options), "language_rendering": "preserve"},
        dict(draft.destinations),
    )
    repository.create_metadata_policy_draft(blocked.canonical_json)
    blocked_resolution = resolve_policy(blocked, report.target.library)
    with pytest.raises(MetadataReportError, match="processing"):
        repository.activate_metadata_policy(
            blocked.policy_id,
            draft.policy_id,
            (
                (
                    report.target.library,
                    blocked_resolution.resolved_id,
                    blocked_resolution.canonical_json,
                ),
            ),
            41,
        )
    repository.request_review_scan(
        report.target.connection, report.target.library, "lib-1", _PRODUCER, 41
    )
    discovery = repository.claim_review_pipeline_action(42)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        1,
        "b" * 64,
        (
            replace(
                _entry(report.target.item, report.file.identity),
                library=report.target.library,
            ),
        ),
        43,
    )
    extraction = repository.claim_review_pipeline_action(44)
    assert extraction is not None
    repository.advance_review_pipeline_action(extraction, report.report_id, 45)
    retried = repository.retry_metadata_policy_transition(transition.transition_id, 46)
    assert (retried.state, retried.failed, retried.total) == ("processing", 0, 1)
    provider_action = repository.claim_review_pipeline_action(47)
    assert provider_action is not None
    assert provider_action.stage == "provider"
    repository.advance_review_pipeline_action(provider_action, None, 48)
    proposal_action = repository.claim_review_pipeline_action(49)
    assert proposal_action is not None
    assert proposal_action.stage == "proposal"
    current_policy = repository.current_resolved_metadata_policy(report.target.library)
    assert current_policy is not None
    successor = evaluate_report(
        report, current_policy.resolved_id, dict(current_policy.destinations)
    )
    repository.publish_proposal(successor)
    repository.advance_review_pipeline_action(
        proposal_action, successor.proposal_set_id, 50
    )
    admission = repository.claim_review_pipeline_action(51)
    assert admission is not None
    admitted = json.loads(repository.admit_review_case(successor))
    repository.advance_review_pipeline_action(
        admission, admitted.get("review_case_id"), 52
    )
    projection = repository.claim_review_pipeline_action(53)
    assert projection is not None
    repository.advance_review_pipeline_action(projection, None, 54)
    completed = repository.metadata_policy_transition()
    assert completed is not None
    assert (completed.state, completed.completed, completed.total) == (
        "completed",
        1,
        1,
    )
    with pytest.raises(MetadataReportError):
        repository.activate_metadata_policy(
            MetadataPolicyProfile.builtin().policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (
                (
                    report.target.library,
                    "resolved_policy_" + "0" * 64,
                    resolved.canonical_json,
                ),
            ),
            41,
        )
    repository.close()


def test_failed_provider_work_restarts_and_retries_at_safe_proposal_stage(
    state_path: Path,
) -> None:
    repository, report, _, _ = _current_case_with_completed_work(state_path)
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='provider',state='queued',"
        "report_id=?,outcome=NULL,updated_at=24",
        (report.report_id,),
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='processing',updated_at=24"
    )
    repository._connection.commit()
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)
    transition = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        25,
    )
    assert transition is not None
    provider = repository.claim_review_pipeline_action(26)
    assert provider is not None
    assert "provider" == provider.stage
    repository.fail_review_pipeline_action(provider, "provider", False, 27)
    failed = repository.metadata_policy_transition()
    assert failed is not None
    assert ("completed_with_failures", 1) == (failed.state, failed.failed)
    repository.close()

    reopened = open_repository(state_path, create=False)
    retried = reopened.retry_metadata_policy_transition(transition.transition_id, 28)
    assert ("processing", 0) == (retried.state, retried.failed)
    action = reopened.claim_review_pipeline_action(29)
    assert action is not None
    assert ("proposal", report.report_id) == (action.stage, action.report_id)
    reopened.close()


def test_failed_extraction_work_restarts_at_extraction_with_fixed_cohort(
    state_path: Path,
) -> None:
    repository, report, _, _ = _current_case_with_completed_work(state_path)
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='extraction',state='queued',"
        "attempt=0,recovery_attempt=0,report_id=NULL,proposal_set_id=NULL,"
        "review_case_id=NULL,failure_code=NULL,outcome=NULL,updated_at=24"
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='processing',updated_at=24"
    )
    repository._connection.commit()
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)
    transition = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        25,
    )
    assert transition is not None
    extraction = repository.claim_review_pipeline_action(26)
    assert extraction is not None
    assert ("extraction", None) == (extraction.stage, extraction.report_id)
    repository.fail_review_pipeline_action(extraction, "failed", False, 27)
    failed = repository.metadata_policy_transition()
    assert failed is not None
    assert ("completed_with_failures", 1, 1) == (
        failed.state,
        failed.failed,
        failed.total,
    )
    fixed_item = repository._connection.execute(
        "SELECT report_id,work_id,state FROM metadata_policy_transition_items"
    ).fetchone()
    assert (report.report_id, extraction.action_id, "failed") == fixed_item
    repository.close()

    reopened = open_repository(state_path, create=False)
    retried = reopened.retry_metadata_policy_transition(transition.transition_id, 28)
    assert ("processing", 0, 1) == (retried.state, retried.failed, retried.total)
    assert (
        "extraction",
        "queued",
        0,
        0,
        None,
        None,
        None,
        None,
    ) == reopened._connection.execute(
        "SELECT stage,state,attempt,recovery_attempt,report_id,proposal_set_id,"
        "review_case_id,failure_code FROM review_preparation_work"
    ).fetchone()
    assert (
        report.report_id,
        extraction.action_id,
        "pending",
    ) == reopened._connection.execute(
        "SELECT report_id,work_id,state FROM metadata_policy_transition_items"
    ).fetchone()
    action = reopened.claim_review_pipeline_action(29)
    assert action is not None
    assert ("extraction", None) == (action.stage, action.report_id)
    reopened.close()


@pytest.mark.parametrize(
    ("stage", "expected_stage", "expected_report"),
    (
        ("extraction", "extraction", None),
        ("provider", "proposal", "fixed"),
    ),
    ids=("extraction", "provider"),
)
def test_activation_restarts_preexisting_terminal_early_work(
    state_directory: Path,
    stage: str,
    expected_stage: str,
    expected_report: str | None,
) -> None:
    path = state_directory / f"terminal-{stage}.sqlite"
    repository, report, _, _ = _current_case_with_completed_work(path)
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage=?,state='failed',"
        "attempt=3,recovery_attempt=2,report_id=?,proposal_set_id=NULL,"
        "review_case_id=NULL,failure_code='failed',outcome=NULL,updated_at=24",
        (stage, None if stage == "extraction" else report.report_id),
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='processing',updated_at=24"
    )
    repository._connection.commit()
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)

    transition = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        25,
    )
    assert transition is not None
    assert ("processing", 0, 1) == (
        transition.state,
        transition.failed,
        transition.total,
    )
    action = repository.claim_review_pipeline_action(26)
    assert action is not None
    assert expected_stage == action.stage
    assert (None if expected_report is None else report.report_id) == action.report_id
    repository.close()


@pytest.mark.parametrize("outcome", ("stale", "provider"))
def test_atomic_extraction_callback_updates_fixed_transition_failure(
    state_directory: Path, outcome: str
) -> None:
    path = state_directory / f"callback-{outcome}.sqlite"
    repository, report, _, _ = _current_case_with_completed_work(path)
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='extraction',state='queued',"
        "attempt=0,recovery_attempt=0,report_id=NULL,proposal_set_id=NULL,"
        "review_case_id=NULL,failure_code=NULL,outcome=NULL,updated_at=24"
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='processing',updated_at=24"
    )
    repository._connection.commit()
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)
    transition = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        25,
    )
    assert transition is not None
    extraction = repository.claim_review_pipeline_action(26)
    assert extraction is not None
    published = _report("Changed title", "c", "d") if outcome == "stale" else report
    repository.publish(
        published,
        lambda: True,
        None,
        lambda value, exact: value.complete_review_extraction_publication(
            extraction,
            exact.report_id,
            27,
            provider_failure_code=("provider" if outcome == "provider" else None),
        ),
    )
    current = repository.metadata_policy_transition()
    assert current is not None
    if outcome == "stale":
        assert ("completed_with_failures", 1) == (current.state, current.failed)
        assert (
            "stale"
            == repository._connection.execute(
                "SELECT failure_code FROM metadata_policy_transition_items"
            ).fetchone()[0]
        )
    else:
        assert ("processing", 0) == (current.state, current.failed)
        proposal = repository.claim_review_pipeline_action(28)
        assert proposal is not None
        assert proposal.stage == "proposal"
    repository.close()


def test_new_library_preview_can_establish_next_generation_resolution(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    first = _draft()
    repository.create_metadata_policy_draft(first.canonical_json)
    first_resolution = resolve_policy(first, "first")
    transition = repository.activate_metadata_policy(
        first.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        (("first", first_resolution.resolved_id, first_resolution.canonical_json),),
        10,
    )
    assert transition is not None
    assert "completed" == transition.state
    second = MetadataPolicyProfile.create(
        "Expanded policy",
        {**dict(first.options), "language_rendering": "preserve"},
        dict(first.destinations),
    )
    repository.create_metadata_policy_draft(second.canonical_json)
    notebook = PolicyNotebook(repository, ("first", "second"), {})

    preview = notebook.preview(second)
    assert ("first", "second") == tuple(item.library for item in preview.libraries)
    configuration = SimpleNamespace(
        libraries=(SimpleNamespace(name="second", policy_override=None),)
    )
    with pytest.raises(MetadataReportError):
        composition._current_resolved_policy(configuration, repository, "second")
    activation = notebook.activate(second.policy_id, first.policy_id, 11)
    assert activation.transition is not None
    assert 3 == activation.transition.generation
    persisted = repository.current_resolved_metadata_policy("second")
    assert persisted is not None
    assert second.policy_id == persisted.profile_id
    repository.close()


def test_cover_automatic_profile_activates_through_the_application_boundary(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    builtin = MetadataPolicyProfile.builtin()
    destinations = dict(builtin.destinations)
    destinations["cover_order"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Automatic confirmed covers", dict(builtin.options), destinations
    )
    repository.create_metadata_policy_draft(profile.canonical_json)

    activation = PolicyNotebook(repository, ("ebooks",), {}).activate(
        profile.policy_id, builtin.policy_id, 11
    )

    assert activation.transition is not None
    persisted = repository.current_resolved_metadata_policy("ebooks")
    assert persisted is not None
    assert dict(persisted.destinations)["cover_order"] == "automatic"
    repository.close()


def test_generation_one_configured_override_binds_and_replays_through_admission(
    state_path: Path,
) -> None:
    repository = open_repository(state_path)
    report = _report()
    repository.publish(report, lambda: True)
    override = {
        "options": {},
        "destinations": {"tags": "ignore"},
    }
    configuration = SimpleNamespace(
        libraries=(
            SimpleNamespace(
                name=report.target.library,
                policy_override=override,
            ),
        )
    )
    resolved = composition._current_resolved_policy(
        configuration, repository, report.target.library
    )
    proposal = evaluate_report(
        report, resolved.resolved_id, dict(resolved.destinations)
    )

    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    assert "admitted" == admitted["status"]
    tags = next(
        result
        for result in proposal.results
        if result.destination is TargetDestination.TAGS
    )
    assert (ProposalOutcome.INELIGIBLE, ("policy_ignored",)) == (
        tags.outcome,
        tags.reasons,
    )
    persisted = repository.current_resolved_metadata_policy(report.target.library)
    assert resolved == persisted
    changed_configuration = SimpleNamespace(
        libraries=(
            SimpleNamespace(
                name=report.target.library,
                policy_override={"options": {}, "destinations": {"tags": "ask"}},
            ),
        )
    )
    with pytest.raises(composition.ReviewCaseError):
        composition._current_resolved_policy(
            changed_configuration, repository, report.target.library
        )
    repository.close()


def test_activation_and_human_decision_commit_orderings_are_atomic(
    state_directory: Path,
) -> None:
    decision_first_path = state_directory / "decision-first.sqlite"
    decision_writer, report, _, case_id = _current_case_with_completed_work(
        decision_first_path
    )
    decision = _human_decision(
        decision_writer, case_id, "decision-before-activation-0001"
    )
    activator = SQLiteMetadataReportRepository(
        sqlite3.connect(
            decision_first_path,
            check_same_thread=False,
            isolation_level=None,
        )
    )
    draft = _draft()
    decision_writer.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)

    decision_writer.record_human_decision(decision)
    transition = activator.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        25,
    )
    assert transition is not None
    history = activator.get_review_case_history(case_id, 10)
    assert any(value["kind"] == "human_decision_recorded" for value in history)
    assert any(
        value["kind"] == "retired" and value["reason"] == "policy_changed"
        for value in history
    )
    assert json.loads(activator.get_review_case(case_id))["current"]
    assert (1, 1) == activator._connection.execute(
        "SELECT (SELECT COUNT(*) FROM human_decisions),"
        "(SELECT COUNT(*) FROM human_decision_events)"
    ).fetchone()
    decision_writer.close()
    activator.close()

    activation_first_path = state_directory / "activation-first.sqlite"
    stale_writer, report, _, case_id = _current_case_with_completed_work(
        activation_first_path
    )
    stale_decision = _human_decision(
        stale_writer, case_id, "decision-after-activation-0001"
    )
    activator = SQLiteMetadataReportRepository(
        sqlite3.connect(
            activation_first_path,
            check_same_thread=False,
            isolation_level=None,
        )
    )
    draft = _draft()
    stale_writer.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)

    transition = activator.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        25,
    )
    assert transition is not None
    with pytest.raises(HumanDecisionError) as raised:
        stale_writer.record_human_decision(stale_decision)
    assert "stale" == raised.value.code
    assert (0, 0) == stale_writer._connection.execute(
        "SELECT (SELECT COUNT(*) FROM human_decisions),"
        "(SELECT COUNT(*) FROM human_decision_events)"
    ).fetchone()
    assert json.loads(stale_writer.get_review_case(case_id))["current"]
    stale_writer.close()
    activator.close()


def test_field_effect_authority_requires_exact_published_checkpoint(
    state_path: Path,
) -> None:
    repository, _, _, case_id = _current_case_with_completed_work(state_path)
    decision = _human_decision(repository, case_id, "checkpoint-authority-0001")
    repository.record_human_decision(decision)
    assert (
        repository.field_effect_authority_source(decision)["review_case_id"] == case_id
    )
    repository._connection.execute(
        "UPDATE current_review_packages SET checkpoint=checkpoint+2 "
        "WHERE review_case_id=?",
        (case_id,),
    )
    repository._connection.commit()
    assert json.loads(repository.get_review_case(case_id))["current"]
    assert not repository._review_case_accepts_authority(case_id)
    with pytest.raises(FieldEffectError, match="authority_binding"):
        repository.field_effect_authority_source(decision)
    repository.close()


def test_verified_human_choice_can_be_corrected_on_the_same_current_case(
    state_path: Path,
) -> None:
    repository, report, _, case_id = _current_case_with_completed_work(state_path)
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    selected = proposal["result"]["alternatives"][0]["value"]
    assert type(selected) is dict
    accepted = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="verified-replacement-accept-0001",
        occurred_at=24,
    )
    repository.record_human_decision(accepted)
    original = next(
        field["value"]
        for field in case.target["fields"]
        if field["destination"] == accepted.destination and field["state"] == "value"
    )
    plan = FieldEffectPlan.create(
        connection=report.target.connection,
        library=report.target.library,
        work_id=report.target.item,
        destination=accepted.destination,
        pre_value=original,
        desired_value=selected["value"],
        target_fingerprint=accepted.target_fingerprint,
        target_version=field_value_version(accepted.destination, original),
        dependency_digests=(accepted.target_fingerprint,),
        authority=EffectAuthority(
            accepted.decision_id,
            accepted.review_case_id,
            accepted.target_fingerprint,
            "persisted_review_case",
            "human",
            "candidate",
            None,
            None,
        ),
        consent=EffectConsent.create(
            connection=report.target.connection,
            library=report.target.library,
            destination=accepted.destination,
            envelope_identity=repository.field_effect_consent_envelope_identity(
                report.target.library
            ),
            granted_at=25,
        ),
        capability_digest="d" * 64,
        created_at=25,
    )
    repository.record_field_effect(FieldEffectOperation(plan, updated_at=25))
    repository.claim_field_effect(plan.plan_id, "planned", "a" * 64, 26)
    repository.transition_field_effect(plan.plan_id, "claimed", "applying", 27)
    repository.transition_field_effect(plan.plan_id, "applying", "verified", 28)

    correction = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=accepted.destination,
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="verified-replacement-keep-0001",
        occurred_at=24,
    )
    repository.record_human_decision(correction)
    source = repository.field_effect_authority_source(correction)
    assert source["pre_value"] == selected["value"]
    assert source["decision_value"] == {"kind": selected["kind"], "value": original}
    assert repository.pending_field_effect_decision_documents(10) == (
        correction.to_json(),
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions WHERE review_case_id=? AND destination=?",
        (case_id, accepted.destination),
    ).fetchone() == (2,)
    assert [
        HumanDecision.from_json(document).decision_id
        for document in repository.get_review_case_decisions(case_id)
        if HumanDecision.from_json(document).destination == accepted.destination
    ] == [accepted.decision_id, correction.decision_id]
    history = [
        event
        for event in repository.get_review_case_history(case_id, 10)
        if event.get("kind") == "human_decision_recorded"
    ]
    assert [event["decision_id"] for event in history] == [
        correction.decision_id,
        accepted.decision_id,
    ]
    assert history[0]["previous_value"] == {
        "kind": selected["kind"],
        "value": selected["value"],
    }
    assert history[0]["selected_value"] == {"kind": selected["kind"], "value": original}
    repository.close()


@pytest.mark.parametrize(
    "state",
    (TargetFieldState.NULL, TargetFieldState.MISSING, TargetFieldState.EMPTY),
    ids=("null", "missing", "empty"),
)
def test_no_incumbent_title_has_no_corrective_keep_current_authority(
    state_path: Path, state: TargetFieldState
) -> None:
    source = _report()
    report = MetadataReport.create(
        target=TargetFacts(
            source.target.connection,
            source.target.library,
            source.target.item,
            (TargetField(TargetDestination.TITLE, state), *source.target.fields[1:]),
        ),
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )
    repository, _, _, case_id = _current_case_with_completed_work(state_path, report)
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    assert repository.pending_field_effect_decision_documents(10) == ()
    assert repository.pending_field_effect_decision_documents(10) == ()
    assert not any(
        item["destination"] == "title"
        and item["result"]["outcome"] == "unresolved_choice"
        for item in case.proposals
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions WHERE review_case_id=? AND destination='title'",
        (case_id,),
    ).fetchone() == (0,)
    repository.close()


# Each of these is rejected today; only the last is unreachable while
# `type(target_fields) is not list` runs after the iteration that
# `next(...)` performs, because iterating an integer raises `TypeError`
# out of the repository instead of the bound failure.
@pytest.mark.parametrize(
    "fields",
    ((), {}, "", 0, {"destination": "title"}, "title", 7),
    ids=(
        "empty_tuple",
        "empty_mapping",
        "empty_string",
        "zero",
        "mapping",
        "string",
        "integer",
    ),
)
def test_field_effect_authority_rejects_a_non_list_fields_projection(
    state_path: Path, fields: object
) -> None:
    """Reject a malformed projection before iterating it, not while."""
    repository, _, _, case_id = _current_case_with_completed_work(state_path)
    decision = _human_decision(repository, case_id, "fields-projection-0001")
    repository.record_human_decision(decision)
    assert (
        repository.field_effect_authority_source(decision)["review_case_id"] == case_id
    )
    with (
        patch.object(
            field_effect_activity,
            "ReviewCase",
            _CaseWithTargetFields(fields),
        ),
        pytest.raises(FieldEffectError, match="authority_binding"),
    ):
        repository.field_effect_authority_source(decision)
    repository.close()


def test_invisible_stale_case_is_not_in_activation_cohort(state_path: Path) -> None:
    repository = open_repository(state_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    changed = _report("Changed current title", "a", "c")
    repository.publish(changed, lambda: True, report.report_id)
    assert 0 == repository.current_unapplied_count(report.target.library)
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)
    transition = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        30,
    )
    assert transition is not None
    assert ("completed", 0, 0, 0) == (
        transition.state,
        transition.completed,
        transition.total,
        transition.failed,
    )
    assert (0,) == repository._connection.execute(
        "SELECT COUNT(*) FROM metadata_policy_transition_items"
    ).fetchone()
    repository.close()


def test_changed_inflight_extraction_is_new_work_not_old_cohort_completion(
    state_path: Path,
) -> None:
    repository, report, _, _ = _current_case_with_completed_work(state_path)
    repository.request_review_scan(
        report.target.connection, report.target.library, "lib-2", _PRODUCER, 24
    )
    discovery = repository.claim_review_pipeline_action(25)
    assert discovery is not None
    changed = _report("Changed title", "c", "d")
    repository.complete_review_discovery(
        discovery,
        2,
        "c" * 64,
        (
            replace(
                _entry(changed.target.item, changed.file.identity),
                library=changed.target.library,
                library_id="lib-2",
                updated_at=2,
            ),
        ),
        26,
    )
    draft = _draft()
    repository.create_metadata_policy_draft(draft.canonical_json)
    resolved = resolve_policy(draft, report.target.library)
    transition = repository.activate_metadata_policy(
        draft.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        30,
    )
    assert transition is not None
    extraction = repository.claim_review_pipeline_action(31)
    assert extraction is not None
    assert "extraction" == extraction.stage
    repository.publish(changed, lambda: True)
    repository.advance_review_pipeline_action(extraction, changed.report_id, 32)
    stale = repository.metadata_policy_transition()
    assert stale is not None
    assert (stale.state, stale.completed, stale.total, stale.failed) == (
        "completed_with_failures",
        0,
        1,
        1,
    )
    assert repository._connection.execute(
        "SELECT report_id,state,failure_code FROM metadata_policy_transition_items"
    ).fetchone() == (report.report_id, "failed", "stale")
    next_action = repository.claim_review_pipeline_action(33)
    assert next_action is not None
    assert "provider" == next_action.stage
    repository.close()


@pytest.mark.parametrize("option", ("add_qualified", "replace_qualified"))
@pytest.mark.parametrize("populated", (False, True))
def test_audio_only_series_options_complete_mixed_media_transition_without_ebook_authority(
    state_path, option, populated
):
    from mechanical_librarian.application.proposal_sets import CreateProposalSet
    from mechanical_librarian.application.review_cases import AdmitReviewCase
    from mechanical_librarian.application.review_pipeline import (
        ReviewPreparationPipeline,
    )
    from mechanical_librarian.domain.automatic_decision import (
        AutomaticDecision,
        AutomaticSafetyState,
    )
    from mechanical_librarian.domain.metadata_report import TargetSeries
    from tests.support.provider_recovery_rail import (
        _automatic_series_report,
        _series_provider,
    )
    from tests.unit.test_audiobook_review_decisions import _audiosilo_consumer_report

    audio = _audiosilo_consumer_report(populated=False, multiple_series=False)
    ebook = _automatic_series_report(
        _series_provider("hardcover", "edition-1", "hardcover"),
        _series_provider("open_library", "edition-2", "open_library"),
    )
    if populated:
        ebook = MetadataReport.create(
            target=replace(
                ebook.target,
                fields=tuple(
                    TargetField(
                        TargetDestination.SERIES,
                        TargetFieldState.VALUE,
                        (TargetSeries("Old Cycle", "1"),),
                    )
                    if field.destination is TargetDestination.SERIES
                    else field
                    for field in ebook.target.fields
                ),
            ),
            file=ebook.file,
            local_claims=ebook.local_claims,
            local_reasons=ebook.local_reasons,
            providers=ebook.providers,
        )
    reports = {report.target.item: report for report in (audio, ebook)}
    repository = open_repository(state_path)
    assert repository.enter_shadow_preview(1) == "preview"
    for report in reports.values():
        baseline = resolve_policy(
            MetadataPolicyProfile.builtin(), report.target.library
        )
        assert (
            repository.bind_initial_metadata_policy_resolution(baseline.canonical_json)
            == baseline.resolved_id
        )
        assert (
            repository.current_resolved_metadata_policy(report.target.library)
            == baseline
        )
    authority = repository.authority
    now = 10

    def factory():
        return open_repository(state_path, create=False, authority=authority)

    def propose(repo, report_id):
        return ProposalSet.from_json(
            CreateProposalSet(
                repo,
                lambda report: report == reports[report.target.item],
                lambda report: repo.current_resolved_metadata_policy(
                    report.target.library
                ),
            ).run(report_id)
        ).proposal_set_id

    def admit(repo, proposal_id):
        def current(proposal):
            report = MetadataReport.from_json(repo.get(proposal.report_id))
            return (
                report == reports[report.target.item]
                and proposal.resolved_policy_id
                == repo.current_resolved_metadata_policy(
                    report.target.library
                ).resolved_id
            )

        result = json.loads(AdmitReviewCase(repo, current).run(proposal_id))
        return result["status"], result.get("review_case_id")

    def project(repo, action):
        return (
            "stale"
            if repo.persist_shadow_outcome(
                action.review_case_id, now, staged_action=action
            )
            == "stale"
            else "complete"
        )

    def extract(repo, action, _claim_next):
        report = reports[action.item_id]
        repo.publish(report, lambda: reports[action.item_id] == report)
        return report.report_id, False

    libraries = tuple(
        ("primary", report.target.library, "lib-1", _PRODUCER)
        for report in (audio, ebook)
    )

    def discover(_connection, library):
        report = next(
            value for value in reports.values() if value.target.library == library
        )
        entry = replace(
            _entry(report.target.item, report.file.identity),
            library=library,
            has_ebook=report.audio is None,
            has_audio=report.audio is not None,
            format=report.file.format,
            service_path="/synthetic.m4b" if report.audio else "/synthetic.epub",
            mount="audiobooks" if report.audio else "books",
        )
        return 1, "a" * 64, (entry,)

    pipeline = ReviewPreparationPipeline(
        repository_factory=factory,
        libraries=libraries,
        discover=discover,
        extract=extract,
        providers=lambda *_: (),
        propose=propose,
        admit=admit,
        project=project,
        clock=lambda: now,
    )
    for _, library, _, _ in libraries:
        pipeline.request("primary", library)
    for _ in range(6):
        if not pipeline.run_next():
            break
    assert all(
        status is not None and status.state == "completed"
        for status in pipeline.status()[0]
    ), pipeline.status()
    profile = MetadataPolicyProfile.create(
        "Audio series in a mixed library",
        {**dict(DEFAULT_OPTIONS), "series": option},
        {
            key: "automatic"
            if key == "series"
            else "unsupported"
            if key == "explicit"
            else "ask"
            for key in OPTION_CATALOGUE
        },
    )
    repository.create_metadata_policy_draft(profile.canonical_json)
    resolutions = tuple(
        resolve_policy(profile, library) for _, library, _, _ in libraries
    )
    transition = repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        tuple(
            (value.library, value.resolved_id, value.canonical_json)
            for value in resolutions
        ),
        21,
    )
    assert transition is not None and transition.total == 2
    now = 30
    for _ in range(6):
        if not pipeline.run_next():
            break
    transition = repository.metadata_policy_transition()
    assert transition is not None
    assert (transition.state, transition.completed, transition.failed) == (
        "completed",
        2,
        0,
    )
    repository.activate_qualified_automatic(31)
    case_ids = []
    for document, _ in repository.list_current_review_case_documents(None, 50, 0, None):
        case = ReviewCase.from_json(document)
        case_ids.append((case.review_case_id, case.media_kind))
        outcome = json.loads(repository.persist_shadow_outcome(case.review_case_id, 32))
        series = next(
            value for value in outcome["outcomes"] if value["destination"] == "series"
        )
        if case.media_kind == "ebook":
            assert series["disposition"] == "abstention"
    repository.close()
    repository = open_repository(state_path)
    for case_id, media in case_ids:
        decisions = [
            AutomaticDecision.from_json(value)
            for value in repository.get_review_case_decisions(case_id)
        ]
        assert bool(decisions) is (media == "audiobook")
        if decisions:
            assert decisions[0].policy_option.endswith(":audiobook_series_fill")
    assert repository.automatic_safety_state() == AutomaticSafetyState(False, None, ())
    repository.close()
