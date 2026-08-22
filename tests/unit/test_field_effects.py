from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeAuthorityCurrentness,
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import PersistedEffectConsent
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.application.operations import StageTimer, aggregate_incidents
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.field_effect import (
    SUPPORTED_DESTINATIONS,
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectNoChange,
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)

if TYPE_CHECKING:
    # Annotation-only: `TimingCollector` stands in for `TimingRepository`, whose
    # `append` takes exactly this record.
    from mechanical_librarian.application.operations import StageTiming

CAPABILITY = "c" * 64
DEPENDENCY = "d" * 64


class Clock:
    def __init__(self) -> None:
        self.value = 10

    def __call__(self) -> int:
        self.value += 1
        return self.value


class TimingCollector:
    def __init__(self) -> None:
        self.values: list[StageTiming] = []

    def append(self, timing) -> None:
        self.values.append(timing)

    def list(self):
        return tuple(self.values)


def decision(destination: str, selected: object, suffix: str = "a") -> HumanDecision:
    return HumanDecision(
        "human_decision_" + suffix * 64,
        "review_case_" + "b" * 64,
        "review_proposal_" + "c" * 64,
        "d" * 64,
        destination,
        "owner",
        "accept",
        selected,
        "e" * 64,
        "f" * 64,
        1,
    )


def envelope(destination: str, value: object) -> dict[str, object]:
    kind = (
        "list"
        if destination in {"authors", "genres", "tags"}
        else "series"
        if destination == "series"
        else "cover"
        if destination == "cover"
        else "text"
    )
    return {"kind": kind, "value": value}


def metadata_report(
    title: str,
    content: str,
    file_identity: str,
    item_id: str = "1234567890abcdef",
    library: str = "books",
) -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Exact evaluator candidate",
        canonical="Exact evaluator candidate",
        excerpt="Exact evaluator candidate",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", library, item_id, default_target_fields(title)),
        file=FileFacts(0, content * 64, file_identity * 64),
        local_claims=(claim,),
        local_reasons=("opening_matter_title",),
        providers=(),
    )


@dataclass
class _EffectEnvironment:
    """The guarded field-effect application under test, with its doubles.

    Declared rather than a `SimpleNamespace` so every member keeps the type
    the `setUp` attributes carried; an untyped namespace would silently make
    `service` and `repository` `Any` and retire the site suppressions that
    depend on their real signatures.
    """

    temporary: tempfile.TemporaryDirectory[str]
    database_path: Path
    repository: SQLiteMetadataReportRepository
    target: FakeTargetService
    authority: FakeAuthorityCurrentness
    dependencies: FakeDependencyCurrentness
    backup: FakeBackupReadiness
    capabilities: FakeCapabilityObserver
    consent: FakeConsentCurrentness
    preclaim: FakePreclaimHook
    claim_hook: FakePreclaimHook
    recovery_hook: FakePreclaimHook
    reevaluation: FakeForwardReevaluation
    recovery: PrivateCoverRecoveryStore
    clock: Clock
    service: GuardedFieldApplication


@pytest.fixture
def effects():
    """One guarded field-effect application with its doubles and store."""

    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    os.chmod(root, 0o700)
    database_path = root / "effects.sqlite"
    repository = open_repository(database_path, cross_thread=True)
    target = FakeTargetService()
    authority = FakeAuthorityCurrentness()
    dependencies = FakeDependencyCurrentness()
    backup = FakeBackupReadiness()
    capabilities = FakeCapabilityObserver(CAPABILITY)
    consent = FakeConsentCurrentness(repository.field_effect_consent_envelope_identity)
    preclaim = FakePreclaimHook()
    claim_hook = FakePreclaimHook()
    recovery_hook = FakePreclaimHook()
    reevaluation = FakeForwardReevaluation()
    recovery = PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover")
    clock = Clock()
    environment = _EffectEnvironment(
        temporary,
        database_path,
        repository,
        target,
        authority,
        dependencies,
        backup,
        capabilities,
        consent,
        preclaim,
        claim_hook,
        recovery_hook,
        reevaluation,
        recovery,
        clock,
        GuardedFieldApplication(
            repository,
            target,
            backup,
            capabilities,
            authority,
            dependencies,
            consent,
            recovery,
            reevaluation,
            clock,
            preclaim,
            claim_hook,
            recovery_hook,
        ),
    )
    yield environment
    environment.repository.close()
    temporary.cleanup()


def test_consent_is_invalidated_when_resolved_policy_semantics_change(
    effects: _EffectEnvironment,
) -> None:
    repository_type = type(effects.repository)
    with patch.object(
        repository_type, "current_resolved_policy_id", return_value="a" * 64
    ):
        first = effects.repository.grant_field_effect_consents(
            "primary", ("books",), ("title",), 1
        )[0]
        assert (
            effects.repository.current_field_effect_consent("primary", "books", "title")
            == first
        )
    with patch.object(
        repository_type, "current_resolved_policy_id", return_value="b" * 64
    ):
        with pytest.raises(FieldEffectError):
            effects.repository.current_field_effect_consent("primary", "books", "title")
        assert not effects.repository.field_effect_consent_is_current(first)
        second = effects.repository.grant_field_effect_consents(
            "primary", ("books",), ("title",), 2
        )[0]
        assert first.consent_id != second.consent_id


def test_activity_ignores_consent_orphaned_by_policy_activation(
    effects: _EffectEnvironment,
) -> None:
    effects.repository.grant_field_effect_consents(
        "primary", ("removed",), ("title",), 1
    )
    builtin = MetadataPolicyProfile.builtin()
    replacement = MetadataPolicyProfile.create(
        "Replacement", dict(builtin.options), dict(builtin.destinations)
    )
    effects.repository.create_metadata_policy_draft(replacement.canonical_json)
    remaining = resolve_policy(replacement, "remaining")
    effects.repository.activate_metadata_policy(
        replacement.policy_id,
        builtin.policy_id,
        (("remaining", remaining.resolved_id, remaining.canonical_json),),
        2,
    )

    activity = effects.repository.field_effect_activity_data(50)

    assert activity["consented_libraries"] == []
    assert activity["consented_destinations"] == []
    policy = effects.repository.field_effect_activity_page(
        search=None,
        event_type="policy",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    # The replacement profile differs from the built-in one only by name, so
    # the transition's own two profiles diff to no changed setting. A Policy
    # row reports that rather than inventing a From/To for a rename.
    assert policy["events"][0]["record"] == {
        "source_kind": "policy_transition",
        "generation": "Policy generation 2",
        "policy_changes": [],
    }


def test_a_policy_row_carries_the_exact_settings_its_own_transition_moved(
    effects: _EffectEnvironment,
) -> None:
    """The From/To comes from the transition's two profiles, not from now.

    The activation below moves one review mode and one representation. Both
    reach the row as the owner's words for the exact settings that moved, and
    the settings that did not move are absent rather than listed as unchanged.
    """
    builtin = MetadataPolicyProfile.builtin()
    options = dict(builtin.options)
    destinations = dict(builtin.destinations)
    # Both settings moved on fields the owner reviews by hand, so the exact
    # From/To under test is not entangled with automatic qualification.
    reviewed = sorted(field for field, value in destinations.items() if value == "ask")
    moved_option, replacement_option = next(
        (field, choice)
        for field in reviewed
        for choice in sorted(OPTION_CATALOGUE[field])
        if choice != options[field]
    )
    moved_destination = next(field for field in reviewed if field != moved_option)
    options[moved_option] = replacement_option
    destinations[moved_destination] = "ignore"
    replacement = MetadataPolicyProfile.create("Replacement", options, destinations)
    effects.repository.create_metadata_policy_draft(replacement.canonical_json)
    remaining = resolve_policy(replacement, "remaining")
    effects.repository.activate_metadata_policy(
        replacement.policy_id,
        builtin.policy_id,
        (("remaining", remaining.resolved_id, remaining.canonical_json),),
        2,
    )

    page = effects.repository.field_effect_activity_page(
        search=None, event_type="policy", occurred_after=0, page=1, page_size=25
    )
    record = page["events"][0]["record"]
    assert record["source_kind"] == "policy_transition"
    changes = record["policy_changes"]
    assert {change["setting"] for change in changes} == {
        "Review mode",
        "Representation",
    }
    review_mode = next(
        change for change in changes if change["setting"] == "Review mode"
    )
    assert (review_mode["previous"], review_mode["next"]) == ("Ask me", "Skip it")
    representation = next(
        change for change in changes if change["setting"] == "Representation"
    )
    assert representation["previous"] != representation["next"]
    assert representation["next"][:1].isupper()
    # Only what moved is named; the profile's other settings are not listed.
    assert len(changes) == 2


def test_activity_fetches_one_current_report_document_for_repeated_events(
    effects: _EffectEnvironment,
) -> None:
    report = metadata_report("Readable work", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    _plan(effects, suffix="a")
    _plan(effects, suffix="b")

    activity = effects.repository.field_effect_activity_data(50)

    assert activity["report_documents"] == {
        report.report_id: report.to_json(),
    }
    assert len(activity["events"]) >= 2
    assert {event["report_id"] for event in activity["events"]} == {report.report_id}
    assert all("report_document" not in event for event in activity["events"])
    page = effects.repository.field_effect_activity_page(
        search="readable work",
        event_type="all",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    assert page["total"] == 2
    assert all(event["work_label"] == "Readable work" for event in page["events"])
    assert all(
        event["record"]["source_kind"] == "field_effect" for event in page["events"]
    )


def test_activity_page_bounds_filters_and_clamps_the_append_only_ledger(
    effects: _EffectEnvironment,
) -> None:
    for index in range(101):
        identity = f"{index:064x}"
        plan_id = "effect_plan_" + identity
        occurred_at = 1_000_000 + index
        event_core = {
            "attempt": 0,
            "decision_id": "human_decision_" + "a" * 64,
            "desired_value": "new",
            "destination": "title",
            "failure": None,
            "kind": "planned",
            "occurred_at": occurred_at,
            "origin": "human",
            "plan_id": plan_id,
            "pre_value": "old",
            "schema_version": "field-effect-event-v1",
            "sequence": 1,
            "state": "planned",
        }
        event_id = (
            "effect_event_"
            + hashlib.sha256(
                json.dumps(event_core, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
        )
        event_document = json.dumps(
            {**event_core, "event_id": event_id},
            separators=(",", ":"),
            sort_keys=True,
        )
        effects.repository._connection.execute(
            "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,work_id,"
            "destination,state,attempt,updated_at,document) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                plan_id,
                "human_decision_" + "a" * 64,
                "primary",
                "books",
                f"book-{index}",
                "title",
                "planned",
                0,
                occurred_at,
                "{}",
            ),
        )
        effects.repository._connection.execute(
            "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) "
            "VALUES(?,?,?,?,?,?)",
            (event_id, plan_id, 1, "planned", occurred_at, event_document),
        )
    effects.repository._connection.commit()
    first = effects.repository.field_effect_activity_page(
        search="book-",
        event_type="review",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    assert first["total"] == 101
    assert len(first["events"]) == 25
    assert first["page"] == 1
    assert first["page_size"] == 25
    assert first["current_waiting"]["state"] == "planned"
    assert [event["occurred_at"] for event in first["events"]] == sorted(
        (event["occurred_at"] for event in first["events"]), reverse=True
    )
    for page_size, expected in ((50, 50), (100, 100)):
        page = effects.repository.field_effect_activity_page(
            search="book-",
            event_type="review",
            occurred_after=0,
            page=1,
            page_size=page_size,
        )
        assert len(page["events"]) == expected
    twenty_six = effects.repository.field_effect_activity_page(
        search="book-",
        event_type="review",
        occurred_after=0,
        page=2,
        page_size=25,
    )
    assert len(twenty_six["events"]) == 25
    last = effects.repository.field_effect_activity_page(
        search="book-1",
        event_type="review",
        occurred_after=0,
        page=1_000_000,
        page_size=25,
    )
    assert last["page"] == last["page_count"]
    assert len(last["events"]) <= 25
    assert all(event["event_type"] == "review" for event in last["events"])
    assert all("book-1" in event["work_id"] for event in last["events"])
    empty = effects.repository.field_effect_activity_page(
        search=None,
        event_type="all",
        occurred_after=2_000_000,
        page=1,
        page_size=25,
    )
    assert (empty["total"], empty["page"], empty["page_count"], empty["events"]) == (
        0,
        1,
        1,
        [],
    )
    assert empty["current_waiting"]["state"] == "planned"
    with pytest.raises(FieldEffectError, match="invalid_activity_query"):
        effects.repository.field_effect_activity_page(
            search="%",
            event_type="hostile",
            occurred_after=-1,
            page=0,
            page_size=26,
        )


def test_activity_page_fails_closed_for_corrupt_or_oversized_durable_rows(
    effects: _EffectEnvironment,
) -> None:
    plan = _plan(effects, suffix="c")
    effects.repository._connection.execute(
        "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) VALUES(?,?,?,?,?,?)",
        ("effect_event_" + "c" * 64, plan.plan.plan_id, 2, "verified", 2, "{not-json"),
    )
    effects.repository._connection.commit()
    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )


def test_activity_page_fails_closed_for_oversized_durable_row(
    effects: _EffectEnvironment,
) -> None:
    plan = _plan(effects, suffix="d")
    effects.repository._connection.execute("PRAGMA ignore_check_constraints=ON")
    effects.repository._connection.execute(
        "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) VALUES(?,?,?,?,?,?)",
        (
            "effect_event_" + "d" * 64,
            plan.plan.plan_id,
            2,
            "verified",
            2,
            "{" + '"x":"' + "x" * 2_097_153 + '"}',
        ),
    )
    effects.repository._connection.execute("PRAGMA ignore_check_constraints=OFF")
    effects.repository._connection.commit()
    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )


def test_activity_page_rolls_back_for_deeply_nested_selected_json(
    effects: _EffectEnvironment,
) -> None:
    plan = _plan(effects, suffix="2")
    nested = "[" * 10_000 + "0" + "]" * 10_000
    effects.repository._connection.execute(
        "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) VALUES(?,?,?,?,?,?)",
        ("effect_event_" + "2" * 64, plan.plan.plan_id, 2, "verified", 2, nested),
    )
    effects.repository._connection.commit()

    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
    assert not effects.repository._connection.in_transaction


def test_activity_summary_reads_no_legacy_events_or_report_documents(
    effects: _EffectEnvironment,
) -> None:
    _plan(effects, suffix="e")
    statements: list[str] = []
    effects.repository._connection.set_trace_callback(statements.append)
    try:
        summary = effects.repository.field_effect_activity_summary()
    finally:
        effects.repository._connection.set_trace_callback(None)

    assert summary["events"] == []
    assert summary["report_documents"] == {}
    sql = "\n".join(statements).casefold()
    assert "from field_effect_events event" not in sql
    assert "from metadata_reports where report_id" not in sql


def test_activity_page_rejects_a_selected_report_digest_mismatch(
    effects: _EffectEnvironment,
) -> None:
    report = metadata_report("Readable work", "5", "6", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    _plan(effects, suffix="f")
    effects.repository._connection.execute("PRAGMA ignore_check_constraints=ON")
    effects.repository._connection.execute("DROP TRIGGER metadata_reports_no_update")
    effects.repository._connection.execute(
        "UPDATE metadata_reports SET document_digest=? WHERE report_id=?",
        ("0" * 64, report.report_id),
    )
    effects.repository._connection.execute("PRAGMA ignore_check_constraints=OFF")
    effects.repository._connection.commit()

    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=2_000_000,
            page=1,
            page_size=25,
        )
    assert not effects.repository._connection.in_transaction


def test_activity_page_rejects_an_oversized_current_waiting_report(
    effects: _EffectEnvironment,
) -> None:
    report = metadata_report("Readable work", "7", "8", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    _plan(effects, suffix="3")
    oversized = "{" + '"x":"' + "x" * 2_097_153 + '"}'
    effects.repository._connection.execute("PRAGMA ignore_check_constraints=ON")
    effects.repository._connection.execute("DROP TRIGGER metadata_reports_no_update")
    effects.repository._connection.execute(
        "UPDATE metadata_reports SET document=?,document_digest=? WHERE report_id=?",
        (oversized, hashlib.sha256(oversized.encode()).hexdigest(), report.report_id),
    )
    effects.repository._connection.execute("PRAGMA ignore_check_constraints=OFF")
    effects.repository._connection.commit()

    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=2_000_000,
            page=1,
            page_size=25,
        )
    assert not effects.repository._connection.in_transaction


def test_activity_page_rejects_a_valid_field_event_that_disagrees_with_columns(
    effects: _EffectEnvironment,
) -> None:
    plan = _plan(effects, suffix="1")
    row = effects.repository._connection.execute(
        "SELECT event_id,document FROM field_effect_events WHERE plan_id=?",
        (plan.plan.plan_id,),
    ).fetchone()
    raw = json.loads(row[1])
    raw["destination"] = "authors"
    effects.repository._connection.execute("PRAGMA recursive_triggers=OFF")
    effects.repository._connection.execute("DROP TRIGGER field_effect_events_no_update")
    effects.repository._connection.execute(
        "UPDATE field_effect_events SET document=? WHERE event_id=?",
        (json.dumps(raw, separators=(",", ":"), sort_keys=True), row[0]),
    )
    effects.repository._connection.commit()

    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )


def test_unused_continuous_consent_survives_exact_schema_validation(
    effects: _EffectEnvironment,
) -> None:
    consent = effects.repository.grant_field_effect_consents(
        "primary", ("books",), ("title",), 1
    )[0]
    effects.repository.close()

    effects.repository = open_repository(effects.database_path, cross_thread=True)

    assert consent == effects.repository.current_field_effect_consent(
        "primary", "books", "title"
    )


def test_matrix_consent_rolls_back_on_second_library_policy_failure(
    effects: _EffectEnvironment,
) -> None:
    original = effects.repository.grant_field_effect_consents(
        "primary",
        ("books",),
        ("title", "subtitle"),
        0,
    )
    repository_type = type(effects.repository)
    with patch.object(
        repository_type,
        "current_resolved_policy_id",
        side_effect=("a" * 64, MetadataReportError("policy")),
    ):
        with pytest.raises(FieldEffectError, match="persistence"):
            effects.repository.grant_field_effect_consent_matrix(
                "primary",
                (("books", ("title",)), ("other", ("subtitle",))),
                1,
            )
    assert effects.repository._connection.execute(
        "SELECT count(*) FROM field_effect_consents WHERE current=1"
    ).fetchone() == (2,)
    assert all(
        effects.repository.field_effect_consent_is_current(consent)
        for consent in original
    )
    assert not effects.repository._connection.in_transaction


def test_matrix_consent_replaces_prior_library_destination_set(
    effects: _EffectEnvironment,
) -> None:
    original = effects.repository.grant_field_effect_consents(
        "primary",
        ("books",),
        ("title", "subtitle"),
        1,
    )

    current = effects.repository.grant_field_effect_consent_matrix(
        "primary",
        (("books", ("title",)),),
        2,
    )

    assert tuple(value.destination for value in current) == ("title",)
    assert effects.repository.field_effect_consent_is_current(current[0])
    assert not effects.repository.field_effect_consent_is_current(original[1])
    with pytest.raises(FieldEffectError, match="consent"):
        effects.repository.current_field_effect_consent("primary", "books", "subtitle")


def test_stale_success_does_not_clear_newer_capability_suspension(
    effects: _EffectEnvironment,
) -> None:
    effects.repository.suspend_field_effect_capability(
        scope="destination",
        connection="primary",
        destination="title",
        reason="field_unavailable",
        changed_at=10,
    )
    effects.repository.clear_field_effect_capability_suspension(
        scope="destination",
        connection="primary",
        destination="title",
        reason="field_unavailable",
        observed_at=9,
    )
    assert (
        effects.repository.field_effect_capability_suspension("primary", "title")
        == "field_unavailable"
    )
    effects.repository.clear_field_effect_capability_suspension(
        scope="destination",
        connection="primary",
        destination="title",
        reason="field_unavailable",
        observed_at=10,
    )
    assert (
        effects.repository.field_effect_capability_suspension("primary", "title")
        == "field_unavailable"
    )
    effects.repository.suspend_field_effect_capability(
        scope="destination",
        connection="primary",
        destination="title",
        reason="unexpected_effect_behavior",
        changed_at=11,
    )
    effects.repository.clear_field_effect_capability_suspension(
        scope="destination",
        connection="primary",
        destination="title",
        reason="field_unavailable",
        observed_at=12,
    )
    assert (
        effects.repository.field_effect_capability_suspension("primary", "title")
        == "unexpected_effect_behavior"
    )
    effects.repository.suspend_field_effect_capability(
        scope="destination",
        connection="primary",
        destination="title",
        reason="insufficient_permission",
        changed_at=12,
    )
    assert (
        effects.repository.field_effect_capability_suspension("primary", "title")
        == "unexpected_effect_behavior"
    )


def test_manual_hold_requires_explicit_return_and_later_human_wins(
    effects: _EffectEnvironment,
) -> None:
    from tests.unit.test_activity_end_to_end import _automatic_candidate_decision

    automatic = _automatic_candidate_decision(effects)
    case_id = automatic.review_case_id
    case = ReviewCase.create_from_data(
        json.loads(effects.repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["proposal_id"] == automatic.proposal_id
    )
    selected = next(
        value
        for value in proposal["result"]["alternatives"]
        if value["alternative_id"] == automatic.candidate_id
    )["value"]
    human = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="manual-hold-human-0001",
        occurred_at=10,
    )
    consent = effects.repository.grant_field_effect_consents(
        "primary", ("ebooks",), (proposal["destination"],), 9
    )[0]
    authority = EffectAuthority(
        automatic.decision_id,
        automatic.review_case_id,
        automatic.target_fingerprint,
        "persisted_review_case",
        "automatic_policy",
        "candidate",
        automatic.resolved_policy_id,
        automatic.qualification_manifest_sha256,
    )
    plan = FieldEffectPlan.create(
        connection="primary",
        library="ebooks",
        work_id="item-1",
        destination=proposal["destination"],
        pre_value=None,
        desired_value=selected["value"],
        target_fingerprint=case.target_fingerprint,
        target_version="version-before-manual",
        dependency_digests=(DEPENDENCY,),
        authority=authority,
        consent=consent,
        capability_digest=CAPABILITY,
        created_at=9,
    )
    automatic_operation = FieldEffectOperation(plan, updated_at=9)
    effects.repository.record_field_effect(automatic_operation)
    effects.repository.record_human_decision(human)
    human_activity = effects.repository.field_effect_activity_page(
        search=None,
        event_type="review",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    assert any(event["state"] == "accept" for event in human_activity["events"])
    human_row = effects.repository._connection.execute(
        "SELECT event_id,document FROM human_decision_events WHERE decision_id=?",
        (human.decision_id,),
    ).fetchone()
    corrupt_human = json.loads(human_row[1])
    corrupt_human["outcome"] = "keep_current"
    effects.repository._connection.execute(
        "DROP TRIGGER human_decision_events_no_update"
    )
    effects.repository._connection.execute(
        "UPDATE human_decision_events SET document=? WHERE event_id=?",
        (
            json.dumps(corrupt_human, separators=(",", ":"), sort_keys=True),
            human_row[0],
        ),
    )
    effects.repository._connection.commit()
    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
    effects.repository._connection.execute(
        "UPDATE human_decision_events SET document=? WHERE event_id=?",
        (human_row[1], human_row[0]),
    )
    effects.repository._connection.execute(
        "CREATE TRIGGER human_decision_events_no_update BEFORE UPDATE ON human_decision_events BEGIN SELECT RAISE(ABORT, 'human decision events are append-only'); END"
    )
    consideration_id = "cover_consideration_" + "7" * 64
    consideration_document = json.dumps(
        {
            "consideration_id": consideration_id,
            "report_id": case.report_id,
            "replay_digest": "8" * 64,
            "review_case_id": case.review_case_id,
            "schema_version": "cover-consideration-v1",
            "supersedes_id": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    effects.repository._connection.execute(
        "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
        (
            consideration_id,
            case.review_case_id,
            case.report_id,
            "8" * 64,
            hashlib.sha256(consideration_document.encode()).hexdigest(),
            None,
            11,
            consideration_document,
        ),
    )
    cover_document = json.dumps(
        {
            "consideration_id": consideration_id,
            "decision_id": None,
            "kind": "automatic_prepared",
            "occurred_at": 11,
            "review_case_id": case.review_case_id,
            "schema_version": "cover-consideration-event-v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    wrong_cover_event_id = "cover_consideration_event_" + "9" * 64
    effects.repository._connection.execute(
        "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
        (
            wrong_cover_event_id,
            consideration_id,
            case.review_case_id,
            "automatic_prepared",
            None,
            11,
            cover_document,
        ),
    )
    effects.repository._connection.commit()
    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
    effects.repository._connection.execute(
        "DROP TRIGGER cover_consideration_events_no_delete"
    )
    effects.repository._connection.execute(
        "DROP TRIGGER cover_considerations_no_delete"
    )
    effects.repository._connection.execute(
        "DELETE FROM cover_consideration_events WHERE event_id=?",
        (wrong_cover_event_id,),
    )
    effects.repository._connection.execute(
        "DELETE FROM cover_considerations WHERE consideration_id=?",
        (consideration_id,),
    )
    effects.repository._connection.execute(
        "CREATE TRIGGER cover_considerations_no_delete BEFORE DELETE ON cover_considerations BEGIN SELECT RAISE(ABORT, 'cover considerations are append-only'); END"
    )
    effects.repository._connection.execute(
        "CREATE TRIGGER cover_consideration_events_no_delete BEFORE DELETE ON cover_consideration_events BEGIN SELECT RAISE(ABORT, 'cover consideration events are append-only'); END"
    )
    effects.repository._connection.commit()
    stale_hold_token = effects.repository.field_effect_manual_hold_token(
        case_id, proposal["destination"]
    )
    assert stale_hold_token is not None
    assert stale_hold_token is not None
    return_scan = effects.repository.request_review_scan(
        "primary", "ebooks", "library-ebooks", "a" * 64, 10
    )
    effects.repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed_with_failures',"
        "refresh_count=3,updated_at=11 WHERE scan_id=?",
        (return_scan.scan_id,),
    )
    effects.repository._connection.commit()
    assert effects.repository.request_field_effect_manual_return(
        case_id,
        proposal["destination"],
        stale_hold_token,
        return_scan.scan_id,
        11,
        11,
    )
    expected_pending = (
        (
            case_id,
            proposal["destination"],
            "primary",
            "ebooks",
            stale_hold_token,
            return_scan.scan_id,
            11,
            11,
        ),
    )
    assert (
        effects.repository.pending_field_effect_manual_returns(100) == expected_pending
    )
    requeued = effects.repository.review_pipeline_progress("primary", "ebooks")
    assert requeued is not None
    assert requeued.state == "queued"
    refresh_count = effects.repository._connection.execute(
        "SELECT refresh_count FROM review_preparation_scans WHERE scan_id=?",
        (return_scan.scan_id,),
    ).fetchone()
    assert refresh_count == (0,)
    effects.repository.close()
    effects.repository = open_repository(
        effects.database_path, create=False, cross_thread=True
    )
    assert (
        effects.repository.pending_field_effect_manual_returns(100) == expected_pending
    )

    assert effects.repository.get_field_effect(plan.plan_id).state == "superseded"
    assert effects.repository.field_effect_manual_hold_context(
        case_id, proposal["destination"]
    ) == ("primary", "ebooks")
    assert len(effects.repository.pending_field_effect_decision_documents(10)) == 1
    assert effects.repository.clear_field_effect_manual_hold(
        case_id, proposal["destination"], 12
    ) == ("primary", "ebooks")
    assert (
        effects.repository.field_effect_manual_hold_context(
            case_id, proposal["destination"]
        )
        is None
    )
    pending = effects.repository.pending_field_effect_decision_documents(10)
    assert len(pending) == 1
    assert automatic.decision_id in pending[0]

    returned = effects.repository.record_field_effect(automatic_operation)
    assert returned.state == "planned"
    assert effects.repository.get_field_effect(plan.plan_id).state == "planned"
    assert tuple(
        json.loads(value)["kind"]
        for value in effects.repository.field_effect_event_documents(plan.plan_id)
    ) == ("planned", "superseded", "planned")

    release_old_scan = threading.Event()
    old_scan_waiting = threading.Event()
    stale_release_result = []

    def complete_old_scan() -> None:
        old_scan_waiting.set()
        assert release_old_scan.wait(2)
        stale_release_result.append(
            effects.repository.clear_field_effect_manual_hold(
                case_id, proposal["destination"], 13, stale_hold_token
            )
        )

    old_scan = threading.Thread(target=complete_old_scan)
    old_scan.start()
    assert old_scan_waiting.wait(1)

    later_human = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="manual-hold-human-0002",
        occurred_at=13,
    )
    effects.repository.record_human_decision(later_human)
    assert effects.repository.get_field_effect(plan.plan_id).state == "superseded"
    assert effects.repository.field_effect_manual_hold_context(
        case_id, proposal["destination"]
    ) == ("primary", "ebooks")
    current_hold_token = effects.repository.field_effect_manual_hold_token(
        case_id, proposal["destination"]
    )
    assert current_hold_token is not None
    assert stale_hold_token != current_hold_token
    release_old_scan.set()
    old_scan.join(2)
    assert not old_scan.is_alive()
    assert stale_release_result == [None]
    effects.repository.close()
    effects.repository = open_repository(
        effects.database_path, create=False, cross_thread=True
    )
    assert (
        effects.repository.field_effect_manual_hold_token(
            case_id, proposal["destination"]
        )
        == current_hold_token
    )
    assert effects.repository.field_effect_manual_hold_context(
        case_id, proposal["destination"]
    ) == ("primary", "ebooks")

    replacement = metadata_report(
        "Replacement title", "3", "4", item_id="item-1", library="ebooks"
    )
    effects.repository.publish(replacement, lambda: True)
    current_policy = effects.repository.current_resolved_metadata_policy("ebooks")
    assert current_policy is not None
    replacement_proposal = evaluate_report(
        replacement,
        current_policy.resolved_id,
        human_review_destinations(current_policy),
    )
    effects.repository.publish_proposal(replacement_proposal)
    effects.repository.admit_review_case(replacement_proposal)
    assert effects.repository.pending_field_effect_decision_documents(10) == ()


def _plan(
    effects: _EffectEnvironment,
    destination: str = "title",
    pre: object = "old",
    desired: object = "new",
    suffix: str = "a",
) -> FieldEffectOperation:
    selected = envelope(destination, desired)
    source = decision(destination, selected, suffix)
    _authorize(effects, source)
    result = effects.service.plan(
        source,
        connection="primary",
        library="books",
        work_id="book-1",
        destination=destination,
        pre_value=pre,
        desired_value=selected,
        target_fingerprint="d" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert result is not None
    effects.target.seed(result.plan)
    return result


def _authorize(
    effects: _EffectEnvironment,
    source: HumanDecision | AutomaticDecision,
    *,
    work_id: str = "book-1",
    target_version: str = "version-1",
) -> None:
    effects.authority.bind(
        source,
        connection="primary",
        library="books",
        work_id=work_id,
        target_version=target_version,
    )
    effects.consent.allow(
        connection="primary", library="books", destination=source.destination
    )


class _PlanWriteWatch:
    """Forward every repository call while watching the one write plan() owns."""

    def __init__(self, repository: object) -> None:
        self._repository = repository
        self.recorded: list[object] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def record_field_effect(self, operation: Any) -> Any:
        self.recorded.append(operation)
        return self._repository.record_field_effect(operation)


class _RaisingAuthority:
    """An authority resolver whose whole behaviour is one chosen failure."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def resolve(self, decision: Any) -> Any:
        raise self.error

    def current(self, authority: Any) -> bool:
        return True


class _RaisingConsent:
    """A consent resolver whose whole behaviour is one chosen failure."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def resolve(self, *, connection: str, library: str, destination: str) -> Any:
        raise self.error

    def current(self, consent: Any) -> bool:
        return True


def _faulted_repository(
    effects: _EffectEnvironment, name: str
) -> SQLiteMetadataReportRepository:
    """Open, then close, a repository in a directory of its very own.

    The private binding takes a *blocking* exclusive `flock` on the database's
    parent directory (`adapters/persistence/binding.py`), so opening a second
    repository beside the fixture's own waits on the fixture's lock forever.
    macOS has no `fcntl`, so the whole lock path is skipped there and a shared
    directory looks harmless -- it deadlocks only on Linux, where the gate runs.
    Give every extra handle its own owner-only directory and neither can wait
    on the other.
    """
    root = Path(effects.temporary.name) / name
    root.mkdir()
    os.chmod(root, 0o700)
    repository = open_repository(root / "faulted.sqlite")
    repository.close()
    return repository


def _plan_title(effects: _EffectEnvironment, source: HumanDecision) -> object:
    return effects.service.plan(
        source,
        connection="primary",
        library="books",
        work_id="book-1",
        destination="title",
        pre_value="old",
        desired_value=envelope("title", "new"),
        target_fingerprint="d" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )


def test_plan_reports_an_authority_storage_fault_and_keeps_a_real_refusal(
    effects: _EffectEnvironment,
) -> None:
    source = decision("title", envelope("title", "new"), "a")
    _authorize(effects, source)
    watch = _PlanWriteWatch(effects.repository)
    effects.service._repository = watch

    fault = FieldEffectError("persistence")
    effects.service._authority = _RaisingAuthority(fault)
    with pytest.raises(GuardedApplicationError) as storage:
        _plan_title(effects, source)

    refusal = KeyError(source.decision_id)
    effects.service._authority = _RaisingAuthority(refusal)
    with pytest.raises(GuardedApplicationError) as unresolved:
        _plan_title(effects, source)

    # The repository named the fault, so the fault's own code reaches the
    # caller; an undeclared failure is all the seam said, so it keeps the
    # refusal code it has today. Neither records a plan row, and both chain.
    assert storage.value.code == "persistence"
    assert storage.value.__cause__ is fault
    assert unresolved.value.code == "decision_value"
    assert unresolved.value.__cause__ is refusal
    assert watch.recorded == []


def test_plan_reports_a_consent_storage_fault_and_keeps_both_real_refusals(
    effects: _EffectEnvironment,
) -> None:
    source = decision("title", envelope("title", "new"), "a")
    _authorize(effects, source)
    watch = _PlanWriteWatch(effects.repository)
    effects.service._repository = watch
    resolver = effects.service._consent

    fault = FieldEffectError("persistence")
    effects.service._consent = _RaisingConsent(fault)
    with pytest.raises(GuardedApplicationError) as storage:
        _plan_title(effects, source)

    withdrawn = FieldEffectError("consent")
    effects.service._consent = _RaisingConsent(withdrawn)
    with pytest.raises(GuardedApplicationError) as unresolvable:
        _plan_title(effects, source)

    unnamed = RuntimeError("the seam did not say")
    effects.service._consent = _RaisingConsent(unnamed)
    with pytest.raises(GuardedApplicationError) as undeclared:
        _plan_title(effects, source)

    effects.service._consent = resolver
    granted = effects.consent.allowed[("primary", "books", "title", "apply_field")]
    effects.consent.revoked.add(granted.consent_id)
    with pytest.raises(GuardedApplicationError) as revoked:
        _plan_title(effects, source)

    assert storage.value.code == "persistence"
    assert storage.value.__cause__ is fault
    assert unresolvable.value.code == "consent"
    assert unresolvable.value.__cause__ is withdrawn
    # An exception nobody named cannot claim `consent`: that code is a 409
    # asserting the recorded state refuses, and this is the one case where the
    # recorded state was never read. It names the seam that could not answer.
    assert undeclared.value.code == "consent_unavailable"
    assert undeclared.value.__cause__ is unnamed
    # A resolved-but-revoked consent is the refusal this method raises itself,
    # which is why it carries no cause: only the relabelling handler chains.
    assert revoked.value.code == "consent"
    assert revoked.value.__cause__ is None
    assert watch.recorded == []


@pytest.mark.parametrize("seam", ("_authority", "_consent"))
def test_plan_reports_a_closed_resolver_database_as_a_storage_fault(
    effects: _EffectEnvironment, seam: str
) -> None:
    source = decision("title", envelope("title", "new"), "a")
    _authorize(effects, source)
    watch = _PlanWriteWatch(effects.repository)
    effects.service._repository = watch
    faulted = _faulted_repository(effects, f"closed{seam}")
    resolvers = {
        "_authority": PersistedFieldEffectAuthority,
        "_consent": PersistedEffectConsent,
    }
    setattr(effects.service, seam, resolvers[seam](faulted))

    with pytest.raises(GuardedApplicationError) as raised:
        _plan_title(effects, source)

    # End to end over both shipped adapters: a closed database is an sqlite
    # fault, the adapter calls it persistence, and plan() no longer overwrites
    # that with a refusal the owner never made.
    assert raised.value.code == "persistence"
    cause = raised.value.__cause__
    assert isinstance(cause, FieldEffectError) and cause.code == "persistence"
    assert isinstance(cause.__cause__, sqlite3.Error)
    assert watch.recorded == []


@pytest.mark.parametrize("seam", ("_authority", "_consent"))
def test_apply_maps_a_faulted_currentness_database_instead_of_escaping(
    effects: _EffectEnvironment, seam: str
) -> None:
    operation = _plan(effects, suffix="9")
    root = Path(effects.temporary.name)
    password = root / f"currentness{seam}.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        effects.repository,
        ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=effects.clock,
        field_application=effects.service,
    )
    faulted = _faulted_repository(effects, f"currentness{seam}")
    resolvers = {
        "_authority": PersistedFieldEffectAuthority,
        "_consent": PersistedEffectConsent,
    }
    setattr(effects.service, seam, resolvers[seam](faulted))

    with TestClient(app, base_url="https://review.test") as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": "https://review.test"},
        )
        assert login.status_code == 200
        response = client.post(
            f"/api/v1/field-effects/{operation.plan.plan_id}/apply",
            headers={
                "Origin": "https://review.test",
                "X-CSRF-Token": login.json()["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            },
        )

    # `current()` answers a bool, so any sqlite fault behind it used to leave
    # the seam as a bare error that no boundary handler names -- a 500 where a
    # mapped refusal belongs. It now arrives as `persistence` and routes to
    # 503, and the plan is untouched either way.
    assert response.status_code == 503, response.text
    assert response.json()["error"] == {"code": "persistence"}
    assert effects.service.status(operation.plan.plan_id).state == "planned"
    assert effects.target.read(operation.plan).value == operation.plan.pre_value


def test_plan_reports_a_persisted_binding_mismatch_as_a_binding_refusal(
    effects: _EffectEnvironment,
) -> None:
    source = decision("title", envelope("title", "new"), "a")
    watch = _PlanWriteWatch(effects.repository)
    effects.service._repository = watch
    effects.service._authority = PersistedFieldEffectAuthority(effects.repository)

    with pytest.raises(GuardedApplicationError) as raised:
        _plan_title(effects, source)

    # A decision the store cannot bind is a refusal the store already named, so
    # `authority_binding` carries through rather than being retitled
    # `decision_value`. It is the same code plan() raises itself for the same
    # species of mismatch, and it is intended, not drift: an unresolved binding
    # and a mismatched one now read alike, and neither reads as an outage.
    assert raised.value.code == "authority_binding"
    cause = raised.value.__cause__
    assert isinstance(cause, FieldEffectError) and cause.code == "authority_binding"
    assert watch.recorded == []


def test_startup_recovery_attempts_every_plan_and_reports_every_failure(
    effects: _EffectEnvironment,
) -> None:
    attempted: list[str] = []
    failing = {"plan-b", "plan-c"}

    class _PendingPlans:
        def __getattr__(self, name: str) -> Any:
            return getattr(effects.repository, name)

        def pending_field_effect_plan_ids(self, limit: int) -> tuple[str, ...]:
            return ("plan-a", "plan-b", "plan-c")

    def apply(plan_id: str) -> None:
        attempted.append(plan_id)
        if plan_id in failing:
            raise GuardedApplicationError(plan_id)

    effects.service._repository = _PendingPlans()
    with patch.object(effects.service, "apply", apply):
        with pytest.raises(ExceptionGroup) as group:
            effects.service.recover_startup()
        # Two plans failed and a third sat behind them; before, only the first
        # failure was reported and the plans after it were never attempted.
        assert attempted == ["plan-a", "plan-b", "plan-c"]
        assert [getattr(error, "code", None) for error in group.value.exceptions] == [
            "plan-b",
            "plan-c",
        ]

        attempted.clear()
        failing.difference_update({"plan-c"})
        with pytest.raises(GuardedApplicationError) as single:
            effects.service.recover_startup()
        # One failure is still raised alone, the shape `recover_pending` uses.
        assert single.value.code == "plan-b"
        assert attempted == ["plan-a", "plan-b", "plan-c"]

        attempted.clear()
        failing.clear()
        assert effects.service.recover_startup() == (3, 0, 0)


def test_every_supported_shape_plans_and_no_ops_do_not_mutate(
    effects: _EffectEnvironment,
) -> None:
    values = {
        "title": "x",
        "subtitle": "x",
        "authors": ["A"],
        "series": [{"name": "S", "sequence": "2.5"}],
        "genres": ["G"],
        "publishedYear": "2026",
        "publishedDate": "2026-01-02",
        "publisher": "P",
        "description": "D",
        "isbn": "1",
        "asin": "A",
        "language": "en",
        "tags": ["T"],
        "cover": "cover_" + "a" * 64,
    }
    assert SUPPORTED_DESTINATIONS == set(values)
    for index, (destination, desired) in enumerate(values.items()):
        suffix = format(index + 1, "x")[-1]
        selected = envelope(destination, desired)
        source = decision(destination, selected, suffix)
        _authorize(effects, source, work_id=f"book-{index}")
        result = effects.service.plan(
            source,
            connection="primary",
            library="books",
            work_id=f"book-{index}",
            destination=destination,
            pre_value=None,
            desired_value=selected,
            target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        )
        assert result.plan.destination == destination  # type: ignore[union-attr]  # reason: `plan` returns `None` only when the desired value matches the current one, which is not this case; a `None` here still fails the test loudly.
    same = decision("title", envelope("title", "same"), "f")
    _authorize(effects, same, work_id="same-book")
    assert (
        effects.service.plan(
            same,
            connection="primary",
            library="books",
            work_id="same-book",
            destination="title",
            pre_value="same",
            desired_value=envelope("title", "same"),
            target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        )
        is None
    )


def test_qualified_automatic_origin_uses_the_identical_plan_and_effect_path(
    effects: _EffectEnvironment,
) -> None:
    automatic = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="title",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="title_layout",
        policy_option="preserve",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        bundle_id="automatic_bundle_" + "4" * 64,
        occurred_at=1,
    )
    effects.authority.automatic_values[automatic.decision_id] = envelope("title", "new")
    _authorize(effects, automatic, work_id="auto-book")
    result = effects.service.plan(
        automatic,
        connection="primary",
        library="books",
        work_id="auto-book",
        destination="title",
        pre_value="old",
        desired_value=envelope("title", "new"),
        target_fingerprint="e" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert result is not None
    assert result.plan.authority.origin == "automatic_policy"
    effects.target.seed(result.plan)
    assert effects.service.apply(result.plan.plan_id).state == "verified"


def test_effect_modules_cannot_construct_audiobookshelf_or_media_writes(
    effects: _EffectEnvironment,
) -> None:
    roots = [
        Path("src/mechanical_librarian/domain/field_effect.py"),
        Path("src/mechanical_librarian/application/field_effects.py"),
        Path("src/mechanical_librarian/adapters/field_effect_fakes.py"),
    ]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in roots)
    assert "audiobookshelf" not in joined.lower()
    assert "media_write" not in joined.lower()
    keep = decision("title", None, "e")
    object.__setattr__(keep, "outcome", "keep_current")
    assert (
        effects.service.plan(
            keep,
            connection="primary",
            library="books",
            work_id="keep-book",
            destination="title",
            pre_value="old",
            desired_value="new",
            target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        )
        is None
    )


def test_verified_sibling_survives_failure_and_restart(
    effects: _EffectEnvironment,
) -> None:
    first = _plan(effects, suffix="1")
    second = _plan(effects, destination="subtitle", suffix="2")
    assert effects.service.apply(first.plan.plan_id).state == "verified"
    effects.target.fail_next = True
    assert effects.service.apply(second.plan.plan_id).state == "retryable"
    effects.repository.close()
    effects.repository = open_repository(
        Path(effects.temporary.name) / "effects.sqlite", create=False, cross_thread=True
    )
    effects.service._repository = effects.repository
    assert effects.service.status(first.plan.plan_id).state == "verified"
    assert effects.service.apply(second.plan.plan_id).state == "verified"


def test_plan_retry_ignores_nonidentity_creation_time(
    effects: _EffectEnvironment,
) -> None:
    original = _plan(effects, suffix="9")
    plan = original.plan
    retried_plan = FieldEffectPlan.create(
        connection=plan.connection,
        library=plan.library,
        work_id=plan.work_id,
        destination=plan.destination,
        pre_value=plan.pre_value,
        desired_value=plan.desired_value,
        target_fingerprint=plan.target_fingerprint,
        target_version=plan.target_version,
        dependency_digests=plan.dependency_digests,
        authority=plan.authority,
        consent=plan.consent,
        capability_digest=plan.capability_digest,
        created_at=plan.created_at + 100,
    )
    assert retried_plan.plan_id == plan.plan_id
    assert retried_plan.created_at != plan.created_at
    reopened = effects.repository.record_field_effect(
        FieldEffectOperation(retried_plan, updated_at=retried_plan.created_at)
    )
    assert reopened == original
    assert effects.repository._connection.execute(
        "SELECT COUNT(*) FROM field_effect_events WHERE plan_id=?",
        (plan.plan_id,),
    ).fetchone() == (1,)


def test_record_rejects_every_noninitial_operation_without_audit_rows(
    effects: _EffectEnvironment,
) -> None:
    source = _plan(effects, suffix="8").plan
    plan = FieldEffectPlan.create(
        connection=source.connection,
        library=source.library,
        work_id="unrecorded-book",
        destination=source.destination,
        pre_value=source.pre_value,
        desired_value=source.desired_value,
        target_fingerprint=source.target_fingerprint,
        target_version=source.target_version,
        dependency_digests=source.dependency_digests,
        authority=source.authority,
        consent=source.consent,
        capability_digest=source.capability_digest,
        created_at=source.created_at + 1,
    )
    initial = FieldEffectOperation(plan, updated_at=plan.created_at)
    invalid = (
        replace(initial, state="claimed", attempt=1, claim_token="a" * 64),
        replace(initial, attempt=1),
        replace(initial, failure="target_failure"),
        replace(initial, claim_token="b" * 64),
        replace(initial, updated_at=plan.created_at + 1),
    )
    for operation in invalid:
        with pytest.raises(FieldEffectError, match="invalid_initial_operation"):
            effects.repository.record_field_effect(operation)
        assert effects.repository._connection.execute(
            "SELECT COUNT(*) FROM field_effects WHERE plan_id=?",
            (plan.plan_id,),
        ).fetchone() == (0,)
        assert effects.repository._connection.execute(
            "SELECT COUNT(*) FROM field_effect_events WHERE plan_id=?",
            (plan.plan_id,),
        ).fetchone() == (0,)


def test_fake_target_serializes_compare_and_write_for_direct_callers(
    effects: _EffectEnvironment,
) -> None:
    first = _plan(effects, suffix="7").plan
    second = FieldEffectPlan.create(
        connection=first.connection,
        library=first.library,
        work_id=first.work_id,
        destination=first.destination,
        pre_value=first.pre_value,
        desired_value="competing-value",
        target_fingerprint=first.target_fingerprint,
        target_version=first.target_version,
        dependency_digests=first.dependency_digests,
        authority=first.authority,
        consent=first.consent,
        capability_digest=first.capability_digest,
        created_at=first.created_at,
    )
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    outcomes: dict[str, object] = {}

    def hold_first(_: FieldEffectPlan) -> None:
        entered.set()
        if not release.wait(2):
            raise RuntimeError("test release timeout")

    effects.target.before_apply = hold_first
    first_thread = threading.Thread(
        target=lambda: outcomes.setdefault("first", effects.target.apply(first))
    )

    def apply_second() -> None:
        outcomes["second"] = effects.target.apply(second)
        second_done.set()

    second_thread = threading.Thread(target=apply_second)
    first_thread.start()
    assert entered.wait(1)
    second_thread.start()
    assert not second_done.wait(0.1)
    release.set()
    first_thread.join(2)
    second_thread.join(2)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert outcomes["first"].outcome == "accepted"
    assert outcomes["second"].outcome == "conflict"


def test_preclaim_failures_are_field_local_and_pause_is_atomic(
    effects: _EffectEnvironment,
) -> None:
    stale = _plan(effects, suffix="3")
    ready = _plan(effects, destination="subtitle", suffix="4")
    effects.dependencies.current_value = False
    assert effects.service.apply(stale.plan.plan_id).failure == "dependency"
    effects.dependencies.current_value = True
    effects.service.set_paused(True)
    with pytest.raises(GuardedApplicationError):
        effects.service.apply(ready.plan.plan_id)
    assert effects.service.status(ready.plan.plan_id).state == "planned"
    effects.service.set_paused(False)
    assert effects.service.apply(ready.plan.plan_id).state == "verified"


def test_capability_failures_persist_the_smallest_scope_and_close_claim_races(
    effects: _EffectEnvironment,
) -> None:
    title = _plan(effects, suffix="1")
    subtitle = _plan(effects, destination="subtitle", suffix="2")
    effects.capabilities.category = "field_unavailable"
    assert effects.service.apply(title.plan.plan_id).failure == "capability"
    assert (
        effects.repository.field_effect_capability_suspension("primary", "title")
        == "field_unavailable"
    )
    effects.capabilities.category = "allowed"
    assert effects.service.apply(subtitle.plan.plan_id).state == "verified"

    authors = _plan(
        effects, destination="authors", pre=["old"], desired=["new"], suffix="3"
    )
    effects.capabilities.category = "connection_unavailable"
    assert effects.service.apply(authors.plan.plan_id).failure == "capability"
    effects.capabilities.category = "allowed"
    genres = _plan(
        effects, destination="genres", pre=["old"], desired=["new"], suffix="4"
    )
    assert effects.service.apply(genres.plan.plan_id).failure == "capability"


def test_atomic_claim_and_unexpected_effect_behavior_honor_durable_suspension(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="5")
    effects.claim_hook.callback = lambda _: (
        effects.repository.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="title",
            reason="field_unavailable",
            changed_at=effects.clock(),
        )
    )
    with pytest.raises(GuardedApplicationError):
        effects.service.apply(operation.plan.plan_id)
    assert effects.service.status(operation.plan.plan_id).state == "planned"

    other = _plan(effects, destination="subtitle", suffix="6")
    effects.target.ambiguous_next = "pre"
    effects.target.suspension_scope_next = "destination"
    effects.service.apply(other.plan.plan_id)
    assert (
        effects.repository.field_effect_capability_suspension("primary", "subtitle")
        == "unexpected_effect_behavior"
    )


def test_final_target_read_persists_typed_connection_unavailability(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="8")
    effects.target.unavailable_scope_next = "connection"
    result = effects.service.apply(operation.plan.plan_id)
    assert (result.state, result.failure) == ("stale", "capability")
    assert (
        effects.repository.field_effect_capability_suspension("primary", "title")
        == "connection_unavailable"
    )


def test_verification_and_reconciliation_persist_typed_unexpected_behavior(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="9")

    def fail_verification(_: object) -> None:
        effects.target.unavailable_scope_next = "destination"
        effects.target.unavailable_reason_next = "unexpected_effect_behavior"

    effects.target.after_apply = fail_verification
    result = effects.service.apply(operation.plan.plan_id)
    assert result.state == "reconciling"
    assert (
        effects.repository.field_effect_capability_suspension("primary", "title")
        == "unexpected_effect_behavior"
    )
    effects.target.unavailable_scope_next = "destination"
    effects.target.unavailable_reason_next = "unexpected_effect_behavior"
    assert effects.service.reconcile(operation.plan.plan_id).state == "retryable"


def test_audit_documents_are_the_persisted_gap_free_event_sequence(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="7")
    verified = effects.service.apply(operation.plan.plan_id)
    documents = effects.repository.field_effect_event_documents(operation.plan.plan_id)
    events = [json.loads(document) for document in documents]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert [event["kind"] for event in events] == [
        "planned",
        "claimed",
        "applying",
        "verified",
    ]
    assert events[-1]["state"] == verified.state


def test_ambiguous_desired_pre_and_third_reconcile_without_blind_retry(
    effects: _EffectEnvironment,
) -> None:
    desired = _plan(effects, suffix="5")
    effects.target.ambiguous_next = "desired"
    assert effects.service.apply(desired.plan.plan_id).state == "verified"

    pre = _plan(effects, destination="subtitle", suffix="6")
    effects.target.ambiguous_next = "pre"
    assert effects.service.apply(pre.plan.plan_id).state == "retryable"
    assert effects.service.apply(pre.plan.plan_id).state == "verified"

    third = _plan(effects, destination="publisher", suffix="7")
    effects.target.ambiguous_next = "third"
    result = effects.service.apply(third.plan.plan_id)
    assert (result.state, result.failure) == ("stale", "third_value")
    actions = effects.repository._connection.execute(
        "SELECT kind,state FROM field_effect_actions WHERE plan_id=? ORDER BY kind",
        (third.plan.plan_id,),
    ).fetchall()
    assert actions == [
        ("forward_reevaluation", "pending"),
        ("targeted_read", "completed"),
    ]
    effects.reevaluation.fail_next = True
    with pytest.raises(RuntimeError, match="reevaluation failure"):
        effects.service.recover_pending()
    assert effects.repository._connection.execute(
        "SELECT state FROM field_effect_actions WHERE plan_id=? "
        "AND kind='forward_reevaluation'",
        (third.plan.plan_id,),
    ).fetchone() == ("claimed",)
    effects.repository.close()
    effects.repository = open_repository(
        Path(effects.temporary.name) / "effects.sqlite",
        create=False,
        cross_thread=True,
    )
    effects.service._repository = effects.repository
    assert effects.service.recover_pending() == (1, 0)
    assert effects.reevaluation.emitted == [third.plan.plan_id]
    assert effects.repository._connection.execute(
        "SELECT state FROM field_effect_actions WHERE plan_id=? "
        "AND kind='forward_reevaluation'",
        (third.plan.plan_id,),
    ).fetchone() == ("completed",)


def test_target_compare_and_set_rejects_a_race_without_overwrite(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="0")
    effects.target.before_apply = lambda plan: effects.target.drift(plan, "external")
    result = effects.service.apply(operation.plan.plan_id)
    assert (result.state, result.failure) == ("stale", "target_changed")
    assert effects.target.read(operation.plan).value == "external"


def test_planning_rejects_rebound_decision_coordinates_and_missing_consent(
    effects: _EffectEnvironment,
) -> None:
    selected = envelope("title", "new")
    source = decision("title", selected, "f")
    _authorize(effects, source)
    cases = (
        {"destination": "subtitle"},
        {"work_id": "other-work"},
        {"target_fingerprint": "e" * 64},
        {"target_version": "version-2"},
    )
    base = {
        "connection": "primary",
        "library": "books",
        "work_id": "book-1",
        "destination": "title",
        "pre_value": "old",
        "desired_value": selected,
        "target_fingerprint": "d" * 64,
        "target_version": "version-1",
        "dependency_digests": (DEPENDENCY,),
        "capability_digest": CAPABILITY,
    }
    for change in cases:
        with pytest.raises(Exception, match="authority_binding"):
            effects.service.plan(source, **(base | change))
    unconsented = decision("publisher", envelope("publisher", "new"), "0")
    effects.authority.bind(
        unconsented,
        connection="primary",
        library="books",
        work_id="book-1",
        target_version="version-1",
    )
    with pytest.raises(Exception, match="consent"):
        effects.service.plan(
            unconsented,
            connection="primary",
            library="books",
            work_id="book-1",
            destination="publisher",
            pre_value="old",
            desired_value=envelope("publisher", "new"),
            target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        )


def test_repository_authority_and_consent_revocation_win_the_claim_race(
    effects: _EffectEnvironment,
) -> None:
    authority = _plan(effects, suffix="1")
    effects.claim_hook.callback = lambda plan: (
        effects.repository.revoke_field_effect_authority(
            plan.authority.decision_id, effects.clock()
        )
    )
    with pytest.raises(Exception, match="claim_conflict"):
        effects.service.apply(authority.plan.plan_id)
    assert effects.service.status(authority.plan.plan_id).state == "superseded"
    assert effects.target.read(authority.plan).value == authority.plan.pre_value

    consent = _plan(effects, destination="subtitle", suffix="2")
    effects.claim_hook.callback = lambda plan: (
        effects.repository.revoke_field_effect_consent(
            plan.consent.consent_id, effects.clock()
        )
    )
    with pytest.raises(Exception, match="claim_conflict"):
        effects.service.apply(consent.plan.plan_id)
    assert effects.service.status(consent.plan.plan_id).state == "superseded"
    assert effects.target.read(consent.plan).value == consent.plan.pre_value


def test_claim_failures_report_the_repository_code_they_actually_hit(
    effects: _EffectEnvironment,
) -> None:
    conflict = _plan(effects, suffix="1")
    effects.claim_hook.callback = lambda plan: (
        effects.repository.revoke_field_effect_authority(
            plan.authority.decision_id, effects.clock()
        )
    )
    with pytest.raises(GuardedApplicationError) as raised:
        effects.service.apply(conflict.plan.plan_id)
    assert raised.value.code == "claim_conflict"
    assert effects.service.status(conflict.plan.plan_id).state == "superseded"

    paused = _plan(effects, destination="subtitle", suffix="2")
    effects.claim_hook.callback = lambda _: effects.repository.set_field_effect_paused(
        True, effects.clock()
    )
    with pytest.raises(GuardedApplicationError) as raised:
        effects.service.apply(paused.plan.plan_id)
    assert raised.value.code == "paused"
    effects.repository.set_field_effect_paused(False, effects.clock())

    capability = _plan(effects, destination="publisher", suffix="3")
    effects.claim_hook.callback = lambda _: (
        effects.repository.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="publisher",
            reason="field_unavailable",
            changed_at=effects.clock(),
        )
    )
    with pytest.raises(GuardedApplicationError) as raised:
        effects.service.apply(capability.plan.plan_id)
    assert raised.value.code == "capability"

    storage = _plan(effects, destination="description", suffix="4")
    with patch.object(
        type(effects.repository),
        "_record_field_effect_event",
        side_effect=sqlite3.OperationalError("disk failure"),
    ):
        with pytest.raises(GuardedApplicationError) as raised:
            effects.service.apply(storage.plan.plan_id)
    assert raised.value.code == "persistence"
    assert effects.service.status(storage.plan.plan_id).state == "planned"
    assert effects.target.read(storage.plan).value == storage.plan.pre_value


def test_cover_cleanup_faults_do_not_masquerade_as_claim_contention(
    effects: _EffectEnvironment,
) -> None:
    cover = _plan(effects, "cover", None, "cover_" + "1" * 64, "b")
    effects.claim_hook.callback = lambda plan: (
        effects.repository.revoke_field_effect_authority(
            plan.authority.decision_id, effects.clock()
        )
    )
    effects.recovery.fail_remove_next = True
    with pytest.raises(GuardedApplicationError) as raised:
        effects.service.apply(cover.plan.plan_id)
    assert raised.value.code == "cover_recovery"
    cause = raised.value.__cause__
    assert cause is not None
    assert isinstance(cause, OSError)
    assert getattr(cause.__context__, "code", None) == "claim_conflict"
    assert effects.service.status(cover.plan.plan_id).state == "superseded"
    assert effects.recovery.exists(cover.plan.plan_id)
    assert effects.repository.field_effect_cleanup_pending(cover.plan.plan_id)


def test_cover_cleanup_separates_a_vanished_row_from_a_storage_fault(
    effects: _EffectEnvironment,
) -> None:
    repository = effects.repository

    class CleanupFaultRepository:
        fault = ""
        reads = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(repository, name)

        def get_field_effect(self, plan_id: str) -> FieldEffectOperation:
            self.reads += 1
            if self.fault and self.reads > 1:
                raise FieldEffectError(self.fault)
            return repository.get_field_effect(plan_id)

    def revoke(plan: FieldEffectPlan) -> None:
        repository.revoke_field_effect_authority(
            plan.authority.decision_id, effects.clock()
        )

    faulting = CleanupFaultRepository()
    effects.service._repository = faulting

    vanished = _plan(effects, "cover", None, "cover_" + "2" * 64, "c")
    faulting.fault, faulting.reads = "not_found", 0
    effects.claim_hook.callback = revoke
    with pytest.raises(GuardedApplicationError) as raised:
        effects.service.apply(vanished.plan.plan_id)
    assert raised.value.code == "claim_conflict"
    assert effects.recovery.exists(vanished.plan.plan_id)

    storage = _plan(effects, "cover", None, "cover_" + "3" * 64, "d")
    faulting.fault, faulting.reads = "persistence", 0
    effects.claim_hook.callback = revoke
    with pytest.raises(GuardedApplicationError) as raised:
        effects.service.apply(storage.plan.plan_id)
    assert raised.value.code == "cover_recovery"
    cause = raised.value.__cause__
    assert cause is not None
    assert getattr(cause, "code", None) == "persistence"
    assert getattr(cause.__context__, "code", None) == "claim_conflict"
    assert effects.recovery.exists(storage.plan.plan_id)


def test_semantic_consent_drift_wins_the_atomic_claim_race(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="e")
    repository_type = type(effects.repository)
    with patch.object(
        repository_type,
        "field_effect_consent_envelope_identity",
        return_value="f" * 64,
    ):
        with pytest.raises(Exception, match="consent"):
            effects.service.apply(operation.plan.plan_id)
    assert effects.service.status(operation.plan.plan_id).state == "planned"
    assert effects.target.read(operation.plan).value == operation.plan.pre_value


def test_newer_report_wins_claim_before_case_retirement(
    effects: _EffectEnvironment,
) -> None:
    report = metadata_report("Old title", "1", "2")
    effects.repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    effects.repository.publish_proposal(proposal)
    case_id = json.loads(effects.repository.admit_review_case(proposal))[
        "review_case_id"
    ]
    case = ReviewCase.create_from_data(
        json.loads(effects.repository.get_review_case(case_id))["case"]
    )
    candidate = next(
        item
        for item in case.proposals
        if item["result"]["outcome"] == "unresolved_choice"
    )
    selected = candidate["result"]["alternatives"][0]["value"]
    source = HumanDecision.create(
        case,
        proposal_id=candidate["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=candidate["destination"],
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="field-effect-newer-report-race",
        occurred_at=1,
    )
    effects.repository.record_human_decision(source)
    effects.authority.bind(
        source,
        connection="primary",
        library="books",
        work_id=report.target.item,
        target_version=case.target_fingerprint,
        source="persisted_review_case",
    )
    effects.consent.allow(
        connection="primary", library="books", destination=source.destination
    )
    operation = effects.service.plan(
        source,
        connection="primary",
        library="books",
        work_id=report.target.item,
        destination=source.destination,
        pre_value="Old title",
        desired_value=selected,
        target_fingerprint=case.target_fingerprint,
        target_version=case.target_fingerprint,
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    effects.target.seed(operation.plan)
    newer = metadata_report("New title", "3", "4")
    effects.claim_hook.callback = lambda _: effects.repository.publish(
        newer, lambda: True
    )
    with pytest.raises(Exception, match="authority"):
        effects.service.apply(operation.plan.plan_id)
    assert effects.service.status(operation.plan.plan_id).state == "planned"
    assert effects.target.read(operation.plan).value == "Old title"


def test_accepted_readback_mismatch_schedules_durable_targeted_read(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="4")
    effects.target.after_apply = lambda plan: effects.target.drift(
        plan, "synthetic-third-value"
    )
    result = effects.service.apply(operation.plan.plan_id)
    assert (result.state, result.failure) == ("reconciling", "readback")
    assert effects.repository._connection.execute(
        "SELECT state FROM field_effect_actions WHERE plan_id=? "
        "AND kind='targeted_read'",
        (operation.plan.plan_id,),
    ).fetchone() == ("pending",)
    effects.repository.close()
    effects.repository = open_repository(
        Path(effects.temporary.name) / "effects.sqlite",
        create=False,
        cross_thread=True,
    )
    effects.service._repository = effects.repository
    assert effects.service.recover_pending() == (2, 0)
    assert effects.service.status(operation.plan.plan_id).state == "stale"


def test_applying_restart_schedules_targeted_read_before_recovery(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="5")
    claimed = effects.repository.claim_field_effect(
        operation.plan.plan_id, "planned", "a" * 64, effects.clock()
    )
    effects.repository.transition_field_effect(
        operation.plan.plan_id, claimed.state, "applying", effects.clock()
    )
    effects.recovery_hook.callback = lambda _: (_ for _ in ()).throw(
        RuntimeError("synthetic restart")
    )
    with pytest.raises(RuntimeError, match="synthetic restart"):
        effects.service.apply(operation.plan.plan_id)
    assert effects.repository._connection.execute(
        "SELECT state FROM field_effect_actions WHERE plan_id=? "
        "AND kind='targeted_read'",
        (operation.plan.plan_id,),
    ).fetchone() == ("pending",)
    effects.repository.close()
    effects.repository = open_repository(
        Path(effects.temporary.name) / "effects.sqlite",
        create=False,
        cross_thread=True,
    )
    effects.service._repository = effects.repository
    assert effects.service.recover_pending() == (1, 0)
    assert effects.service.status(operation.plan.plan_id).state == "retryable"


def test_persisted_targeted_read_reopens_after_restart(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="3")
    effects.target.ambiguous_next = "desired"
    effects.recovery_hook.callback = lambda _: (_ for _ in ()).throw(
        RuntimeError("synthetic restart")
    )
    with pytest.raises(RuntimeError, match="synthetic restart"):
        effects.service.apply(operation.plan.plan_id)
    assert effects.service.status(operation.plan.plan_id).state == "reconciling"
    effects.repository.close()
    effects.repository = open_repository(
        Path(effects.temporary.name) / "effects.sqlite", create=False, cross_thread=True
    )
    effects.service._repository = effects.repository
    assert effects.service.recover_pending() == (1, 0)
    assert effects.service.status(operation.plan.plan_id).state == "verified"
    assert effects.repository._connection.execute(
        "SELECT state,attempt FROM field_effect_actions WHERE plan_id=? AND kind='targeted_read'",
        (operation.plan.plan_id,),
    ).fetchone() == ("completed", 1)


def test_final_preclaim_refresh_blocks_guard_drift_and_leaves_siblings_local(
    effects: _EffectEnvironment,
) -> None:
    cases = (
        ("pause", lambda plan: effects.service.set_paused(True)),
        (
            "authority",
            lambda plan: effects.authority.revoked.add(plan.authority.decision_id),
        ),
        ("capability", lambda plan: setattr(effects.capabilities, "digest", "e" * 64)),
        (
            "dependency",
            lambda plan: setattr(effects.dependencies, "current_value", False),
        ),
    )
    for index, (failure, mutate) in enumerate(cases, start=1):
        operation = _plan(effects, destination="subtitle", suffix=format(index, "x"))
        effects.preclaim.callback = mutate
        if failure == "pause":
            with pytest.raises(Exception, match="paused"):
                effects.service.apply(operation.plan.plan_id)
            result = effects.service.status(operation.plan.plan_id)
            effects.service.set_paused(False)
            assert (result.state, result.attempt) == ("planned", 0)
        else:
            result = effects.service.apply(operation.plan.plan_id)
            assert (result.state, result.failure, result.attempt) == (
                "stale",
                failure,
                0,
            )
        assert effects.target.read(operation.plan).value == operation.plan.pre_value
        effects.capabilities.digest = CAPABILITY
        effects.dependencies.current_value = True

    sibling = _plan(effects, destination="publisher", suffix="e")
    assert effects.service.apply(sibling.plan.plan_id).state == "verified"


def test_supersession_does_not_interrupt_claimed_and_cover_bytes_are_private(
    effects: _EffectEnvironment,
) -> None:
    planned = _plan(effects, suffix="8")
    assert effects.service.supersede(planned.plan.authority.decision_id) == 1
    assert effects.service.status(planned.plan.plan_id).state == "superseded"

    claimed = _plan(effects, destination="subtitle", suffix="a")
    effects.repository.claim_field_effect(
        claimed.plan.plan_id, "planned", "1" * 64, effects.clock()
    )
    assert effects.service.supersede(claimed.plan.authority.decision_id) == 0
    assert effects.service.status(claimed.plan.plan_id).state == "claimed"
    assert effects.service.apply(claimed.plan.plan_id).state == "verified"

    cover_value = "cover_" + "9" * 64
    cover = _plan(effects, "cover", None, cover_value, "9")
    assert effects.service.apply(cover.plan.plan_id).state == "verified"
    assert not effects.recovery.exists(cover.plan.plan_id)
    rows = effects.repository._connection.execute(
        "SELECT document FROM field_effect_events WHERE plan_id=?",
        (cover.plan.plan_id,),
    ).fetchall()
    assert "old-cover" not in repr(rows)


def test_retry_exhaustion_opens_the_field_circuit_without_rolling_back_siblings(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="c")
    for attempt in range(3):
        effects.target.fail_next = True
        result = effects.service.apply(operation.plan.plan_id)
        assert (result.state, result.attempt) == ("retryable", attempt + 1)
    result = effects.service.apply(operation.plan.plan_id)
    assert (result.state, result.failure) == ("stale", "circuit_open")


def test_effect_stages_emit_advisory_timing_and_drain_rejects_only_new_claims(
    effects: _EffectEnvironment,
) -> None:
    timings = TimingCollector()
    ticks = iter(range(0, 100_000_000, 1_000_000))
    effects.service.attach_timing(
        StageTimer(timings, monotonic_ns=lambda: next(ticks), clock=lambda: 99)
    )
    verified = _plan(effects, suffix="1")
    assert effects.service.apply(verified.plan.plan_id).state == "verified"
    ambiguous = _plan(effects, destination="publisher", suffix="2")
    effects.target.ambiguous_next = "desired"
    assert effects.service.apply(ambiguous.plan.plan_id).state == "verified"
    assert [(value.stage, value.wait_class) for value in timings.values] == [
        ("apply", "target"),
        ("verification", "target"),
        ("apply", "target"),
        ("reconciliation", "recovery"),
    ]

    planned = _plan(effects, destination="subtitle", suffix="3")
    effects.service.begin_drain()
    with pytest.raises(Exception, match="draining"):
        effects.service.apply(planned.plan.plan_id)
    assert effects.service.status(planned.plan.plan_id).state == "planned"


def test_drain_closes_the_final_check_to_atomic_claim_race(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="4")
    hook_entered = threading.Event()
    release_hook = threading.Event()
    failures = []

    def block_before_final_admission(_):
        hook_entered.set()
        assert release_hook.wait(2)

    effects.claim_hook.callback = block_before_final_admission

    def apply() -> None:
        try:
            effects.service.apply(operation.plan.plan_id)
        except Exception as error:
            failures.append(error)

    worker = threading.Thread(target=apply)
    worker.start()
    assert hook_entered.wait(1)
    effects.service.begin_drain()
    release_hook.set()
    worker.join(2)
    assert not worker.is_alive()
    assert [getattr(error, "code", None) for error in failures] == ["draining"]
    assert effects.service.status(operation.plan.plan_id).state == "planned"
    assert effects.target.read(operation.plan).value == operation.plan.pre_value


def test_startup_recovers_claimed_applying_and_reconciling_checkpoints(
    effects: _EffectEnvironment,
) -> None:
    claimed = _plan(effects, suffix="1")
    effects.repository.claim_field_effect(
        claimed.plan.plan_id, "planned", "1" * 64, effects.clock()
    )
    applying = _plan(effects, destination="subtitle", suffix="2")
    effects.repository.claim_field_effect(
        applying.plan.plan_id, "planned", "2" * 64, effects.clock()
    )
    effects.repository.transition_field_effect(
        applying.plan.plan_id, "claimed", "applying", effects.clock()
    )
    reconciling = _plan(effects, destination="publisher", suffix="3")
    effects.repository.claim_field_effect(
        reconciling.plan.plan_id, "planned", "3" * 64, effects.clock()
    )
    effects.repository.transition_field_effect(
        reconciling.plan.plan_id, "claimed", "applying", effects.clock()
    )
    effects.repository.schedule_field_effect_reconciliation(
        reconciling.plan.plan_id, "applying", effects.clock()
    )

    assert effects.service.start() == (3, 0, 0)
    assert effects.service.status(claimed.plan.plan_id).state == "verified"
    assert effects.service.status(applying.plan.plan_id).state == "retryable"
    assert effects.service.status(reconciling.plan.plan_id).state == "retryable"


def test_startup_contains_reconciliation_read_failure_and_continues_siblings(
    effects: _EffectEnvironment,
) -> None:
    first = _plan(effects, destination="subtitle", suffix="5")
    second = _plan(effects, destination="publisher", suffix="6")
    for operation, token in ((first, "5"), (second, "6")):
        effects.repository.claim_field_effect(
            operation.plan.plan_id, "planned", token * 64, effects.clock()
        )
        effects.repository.transition_field_effect(
            operation.plan.plan_id, "claimed", "applying", effects.clock()
        )
        effects.repository.schedule_field_effect_reconciliation(
            operation.plan.plan_id, "applying", effects.clock()
        )
    effects.target.fail_read_next = True

    assert effects.service.start() == (2, 0, 0)
    failed = effects.service.status(first.plan.plan_id)
    sibling = effects.service.status(second.plan.plan_id)
    assert (failed.state, failed.failure) == ("retryable", "target_failure")
    assert (sibling.state, sibling.failure) == ("retryable", "not_applied")


def test_startup_contains_verification_read_failure_after_successful_apply(
    effects: _EffectEnvironment,
) -> None:
    first = _plan(effects, destination="subtitle", suffix="7")
    sibling = _plan(effects, destination="publisher", suffix="8")
    for operation, token in ((first, "7"), (sibling, "8")):
        effects.repository.claim_field_effect(
            operation.plan.plan_id, "planned", token * 64, effects.clock()
        )

    effects.target.fail_read_after_apply_for.add(first.plan.plan_id)

    assert effects.service.start() == (2, 1, 0)
    failed = effects.service.status(first.plan.plan_id)
    continued = effects.service.status(sibling.plan.plan_id)
    assert (failed.state, failed.failure) == ("retryable", "target_failure")
    assert (continued.state, continued.failure) == ("verified", None)
    recovery_signal = next(
        value
        for value in effects.repository.operational_snapshot()["signals"]
        if value["work_id"] == first.plan.work_id
        and value["field"] == first.plan.destination
    )
    assert (recovery_signal["kind"], recovery_signal["cause"]) == (
        "automatic_recovery",
        "in_progress",
    )
    effects.target.fail_reads_for.remove(first.plan.plan_id)
    assert effects.target.read(first.plan).value == first.plan.desired_value


def test_shared_backup_and_service_failures_project_above_field_scope(
    effects: _EffectEnvironment,
) -> None:
    failures = (
        (_plan(effects, destination="title", suffix="a"), "backup"),
        (_plan(effects, destination="subtitle", suffix="b"), "backup"),
        (_plan(effects, destination="publisher", suffix="c"), "capability"),
        (_plan(effects, destination="description", suffix="d"), "capability"),
        (_plan(effects, destination="isbn", suffix="e"), "third_value"),
        (_plan(effects, destination="asin", suffix="f"), "third_value"),
    )
    for operation, failure in failures:
        effects.repository.transition_field_effect(
            operation.plan.plan_id,
            "planned",
            "stale",
            effects.clock(),
            failure,
        )

    signals = effects.repository.operational_snapshot()["signals"]
    backup = [value for value in signals if value["kind"] == "backup_failure"]
    service = [value for value in signals if value["kind"] == "service_incompatibility"]
    currentness = [
        value for value in signals if value["cause"] == "currentness_conflict"
    ]
    assert {
        (value["scope"], value["library"], value["work_id"], value["field"])
        for value in backup
    } == {("library", "books", None, None)}
    assert {
        (value["scope"], value["library"], value["work_id"], value["field"])
        for value in service
    } == {("connection", None, None, None)}
    assert {
        (value["scope"], value["work_id"], value["field"]) for value in currentness
    } == {("field", "book-1", "isbn"), ("field", "book-1", "asin")}
    incidents = aggregate_incidents(signals)
    assert sorted(
        (value.kind, value.count)
        for value in incidents
        if value.kind
        in {
            "backup_failure",
            "service_incompatibility",
            "owner_action_required",
        }
    ) == [
        ("backup_failure", 2),
        ("owner_action_required", 1),
        ("owner_action_required", 1),
        ("service_incompatibility", 2),
    ]


def test_cover_recovery_survives_restart_and_rejects_permission_drift(
    effects: _EffectEnvironment,
) -> None:
    cover = _plan(effects, "cover", None, "cover_" + "7" * 64, "d")
    effects.recovery.capture(cover.plan)
    restarted = PrivateCoverRecoveryStore(
        Path(effects.temporary.name) / "recovery",
        lambda _: (_ for _ in ()).throw(RuntimeError("must not reread")),
    )
    temporary_file = next(
        (Path(effects.temporary.name) / "recovery").iterdir()
    ).with_suffix(".recovery.tmp")
    temporary_file.write_bytes(b"incomplete")
    os.chmod(temporary_file, 0o600)
    restarted.capture(cover.plan)
    assert not temporary_file.exists()
    recovery_file = next((Path(effects.temporary.name) / "recovery").iterdir())
    os.chmod(recovery_file, 0o644)
    with pytest.raises(ValueError):
        restarted.capture(cover.plan)
    assert not recovery_file.exists()

    absent_root = Path(effects.temporary.name) / "absent-recovery"
    absent = PrivateCoverRecoveryStore(absent_root, lambda _: None)
    absent.capture(cover.plan)
    assert next(absent_root.iterdir()).read_bytes() == b"ABSENT\n"


def test_cover_terminal_cleanup_obligation_survives_failure_and_restart(
    effects: _EffectEnvironment,
) -> None:
    cover = _plan(effects, "cover", None, "cover_" + "6" * 64, "4")
    effects.recovery.fail_remove_next = True
    assert effects.service.apply(cover.plan.plan_id).state == "verified"
    assert effects.recovery.exists(cover.plan.plan_id)
    assert effects.repository.field_effect_cleanup_pending(cover.plan.plan_id)
    assert effects.repository._connection.execute(
        "SELECT state,attempt FROM field_effect_cleanups WHERE plan_id=?",
        (cover.plan.plan_id,),
    ).fetchone() == ("pending", 1)
    effects.repository.close()
    effects.repository = open_repository(
        Path(effects.temporary.name) / "effects.sqlite", create=False, cross_thread=True
    )
    effects.service._repository = effects.repository
    assert effects.service.recover_pending() == (0, 1)
    assert effects.service.status(cover.plan.plan_id).state == "verified"
    assert not effects.recovery.exists(cover.plan.plan_id)
    assert not effects.repository.field_effect_cleanup_pending(cover.plan.plan_id)


def test_failing_restart_action_cannot_starve_pending_cover_cleanup(
    effects: _EffectEnvironment,
) -> None:
    third = _plan(effects, destination="publisher", suffix="3")
    effects.target.ambiguous_next = "third"
    assert effects.service.apply(third.plan.plan_id).state == "stale"

    cover = _plan(effects, "cover", None, "cover_" + "7" * 64, "7")
    effects.recovery.fail_remove_next = True
    assert effects.service.apply(cover.plan.plan_id).state == "verified"
    assert effects.repository.field_effect_cleanup_pending(cover.plan.plan_id)

    effects.reevaluation.fail_next = True
    with pytest.raises(RuntimeError, match="reevaluation failure"):
        effects.service.recover_pending()
    assert not effects.recovery.exists(cover.plan.plan_id)
    assert not effects.repository.field_effect_cleanup_pending(cover.plan.plan_id)
    assert effects.repository._connection.execute(
        "SELECT state FROM field_effect_actions WHERE plan_id=? "
        "AND kind='forward_reevaluation'",
        (third.plan.plan_id,),
    ).fetchone() == ("claimed",)

    assert effects.service.recover_pending() == (1, 0)
    assert effects.reevaluation.emitted == [third.plan.plan_id]


def test_supersede_runs_cover_cleanup_and_retains_a_failed_obligation(
    effects: _EffectEnvironment,
) -> None:
    cover = _plan(effects, "cover", None, "cover_" + "5" * 64, "8")
    effects.recovery.capture(cover.plan)
    effects.recovery.fail_remove_next = True
    assert effects.service.supersede(cover.plan.authority.decision_id) == 1
    assert effects.repository.field_effect_cleanup_pending(cover.plan.plan_id)
    assert effects.recovery.exists(cover.plan.plan_id)
    assert effects.service.recover_pending() == (0, 1)
    assert not effects.recovery.exists(cover.plan.plan_id)


def test_exact_current_schema_rejects_drift(effects: _EffectEnvironment) -> None:
    effects.repository.close()
    path = Path(effects.temporary.name) / "effects.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE field_effects ADD COLUMN drift TEXT")
    connection.commit()
    connection.close()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    effects.repository = open_repository(Path(effects.temporary.name) / "fresh.sqlite")


def test_existing_database_rejects_extra_operation_members_without_mutation(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="5")
    raw = operation.to_data()
    raw["legacy"] = True
    _corrupt_and_reject(
        effects,
        "UPDATE field_effects SET document=? WHERE plan_id=?",
        (json.dumps(raw), operation.plan.plan_id),
    )


def test_existing_database_rejects_row_document_mismatch(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="6")
    _corrupt_and_reject(
        effects,
        "UPDATE field_effects SET work_id='other-work' WHERE plan_id=?",
        (operation.plan.plan_id,),
    )


def test_existing_database_rejects_malformed_persisted_action(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="7")
    effects.target.ambiguous_next = "desired"
    effects.service.apply(operation.plan.plan_id)
    row = effects.repository._connection.execute(
        "SELECT action_id,document FROM field_effect_actions WHERE plan_id=?",
        (operation.plan.plan_id,),
    ).fetchone()
    raw = json.loads(row[1])
    raw["unknown"] = "legacy"
    _corrupt_and_reject(
        effects,
        "UPDATE field_effect_actions SET document=? WHERE action_id=?",
        (json.dumps(raw), row[0]),
    )


def test_existing_database_rejects_boolean_action_attempt(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="9")
    effects.target.ambiguous_next = "desired"
    effects.service.apply(operation.plan.plan_id)
    row = effects.repository._connection.execute(
        "SELECT action_id,document FROM field_effect_actions WHERE plan_id=?",
        (operation.plan.plan_id,),
    ).fetchone()
    raw = json.loads(row[1])
    raw["attempt"] = True
    _corrupt_and_reject(
        effects,
        "UPDATE field_effect_actions SET document=? WHERE action_id=?",
        (json.dumps(raw), row[0]),
    )


def test_existing_database_rejects_boolean_event_sequence(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="a")
    path = Path(effects.temporary.name) / "effects.sqlite"
    effects.repository.close()
    connection = sqlite3.connect(path)
    trigger = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='field_effect_events_no_update'"
    ).fetchone()[0]
    connection.execute("DROP TRIGGER field_effect_events_no_update")
    row = connection.execute(
        "SELECT event_id,document FROM field_effect_events WHERE plan_id=?",
        (operation.plan.plan_id,),
    ).fetchone()
    raw = json.loads(row[1])
    raw["sequence"] = True
    connection.execute(
        "UPDATE field_effect_events SET document=? WHERE event_id=?",
        (json.dumps(raw), row[0]),
    )
    connection.execute(trigger)
    connection.commit()
    connection.close()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    effects.repository = open_repository(Path(effects.temporary.name) / "fresh.sqlite")
    effects.service._repository = effects.repository


def _corrupt_and_reject(
    effects: _EffectEnvironment, statement: str, parameters: tuple[object, ...]
) -> None:
    path = Path(effects.temporary.name) / "effects.sqlite"
    effects.repository.close()
    connection = sqlite3.connect(path)
    connection.execute(statement, parameters)
    connection.commit()
    connection.close()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    effects.repository = open_repository(Path(effects.temporary.name) / "fresh.sqlite")
    effects.service._repository = effects.repository


def test_authenticated_api_uses_the_same_service_and_blocks_anonymous_mutation(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="b")
    root = Path(effects.temporary.name)
    password = root / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        effects.repository,
        ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=effects.clock,
        field_application=effects.service,
    )
    with TestClient(app, base_url="https://review.test") as client:
        route = f"/api/v1/field-effects/{operation.plan.plan_id}/apply"
        assert (
            client.post(route, headers={"Origin": "https://review.test"}).status_code
            == 401
        )
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": "https://review.test"},
        )
        assert login.status_code == 200
        response = client.post(
            route,
            headers={
                "Origin": "https://review.test",
                "X-CSRF-Token": login.json()["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "verified"
        status = client.get(f"/api/v1/field-effects/{operation.plan.plan_id}")
        assert status.json()["state"] == "verified"


def test_web_maps_permanent_refusals_to_conflict_and_outages_to_unavailable(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="f")
    root = Path(effects.temporary.name)
    password = root / "conflict-owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        effects.repository,
        ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=effects.clock,
        field_application=effects.service,
    )
    # 409 is every code meaning the recorded state refuses this effect, which a
    # decision resolves and a retry never does; 503 is every code meaning the
    # machinery could not answer. `capability`, `authority` and `consent` are
    # permanent refusals and moved from the second to the first.
    expected = {
        "authority": 409,
        "capability": 409,
        "claim_conflict": 409,
        "consent": 409,
        "draining": 409,
        "paused": 409,
        "transition_conflict": 409,
        "cover_recovery": 503,
        "persistence": 503,
        # Both undeclared-seam fallbacks must stay out of the conflict set:
        # neither read the recorded state, so neither may assert it refused.
        "consent_unavailable": 503,
        "decision_value": 503,
        "not_found": 404,
    }
    observed: dict[str, int] = {}
    bodies: dict[str, object] = {}
    route = f"/api/v1/field-effects/{operation.plan.plan_id}/apply"
    with TestClient(app, base_url="https://review.test") as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": "https://review.test"},
        )
        assert login.status_code == 200
        headers = {
            "Origin": "https://review.test",
            "X-CSRF-Token": login.json()["csrf_token"],
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        }
        for code in expected:

            def refuse(plan_id: str, code: str = code) -> None:
                raise GuardedApplicationError(code)

            with patch.object(effects.service, "apply", refuse):
                response = client.post(route, headers=headers)
            observed[code] = response.status_code
            bodies[code] = response.json()["error"]

    assert observed == expected
    # The code itself is untouched: only the status it maps to moved.
    assert bodies == {code: {"code": code} for code in expected}


def test_web_pause_and_resume_bound_persistence_failures(
    effects: _EffectEnvironment,
) -> None:
    repository = effects.repository

    class FailingControlRepository:
        def __getattr__(self, name: str):
            return getattr(repository, name)

        def set_field_effect_paused(self, paused: bool, changed_at: int) -> None:
            raise FieldEffectError("persistence")

    failing = FailingControlRepository()
    effects.service._repository = failing
    root = Path(effects.temporary.name)
    password = root / "failing-control-owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        failing,
        ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=effects.clock,
        field_application=effects.service,
    )
    with TestClient(app, base_url="https://review.test") as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": "https://review.test"},
        )
        assert login.status_code == 200
        headers = {
            "Origin": "https://review.test",
            "X-CSRF-Token": login.json()["csrf_token"],
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        }
        for action in ("pause", "resume"):
            response = client.post(f"/api/v1/field-effects/{action}", headers=headers)
            assert response.status_code == 503
            assert response.json() == {
                "error": {"code": "persistence"},
                "schema_version": "review-error-v1",
            }


def test_web_field_effect_and_review_reads_share_one_repository_lock(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="c")
    entered = threading.Event()
    release = threading.Event()
    library_entered = threading.Event()
    repository = effects.repository

    class BlockingRepository:
        def __getattr__(self, name: str):
            return getattr(repository, name)

        def get_field_effect(self, plan_id: str):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test release timeout")
            return repository.get_field_effect(plan_id)

        def read_review_library(self, search, limit, offset, attention):
            library_entered.set()
            return repository.read_review_library(search, limit, offset, attention)

    blocking = BlockingRepository()
    effects.service._repository = blocking
    root = Path(effects.temporary.name)
    password = root / "serialized-owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        blocking,
        ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=effects.clock,
        field_application=effects.service,
    )
    responses: dict[str, Any] = {}
    with TestClient(app, base_url="https://review.test") as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": "https://review.test"},
        )
        assert login.status_code == 200

        status_thread = threading.Thread(
            target=lambda: responses.setdefault(
                "status", client.get(f"/api/v1/field-effects/{operation.plan.plan_id}")
            )
        )
        library_thread = threading.Thread(
            target=lambda: responses.setdefault(
                "library", client.get("/api/v1/library")
            )
        )
        status_thread.start()
        assert entered.wait(1)
        library_thread.start()
        assert not library_entered.wait(0.1)
        release.set()
        status_thread.join(2)
        library_thread.join(2)
        assert not status_thread.is_alive()
        assert not library_thread.is_alive()
        assert library_entered.is_set()
        assert responses["status"].status_code == 200
        assert responses["library"].status_code == 200


def test_no_change_identity_is_canonical_and_closed() -> None:
    values = dict(
        decision_id="automatic_decision_" + "a" * 64,
        review_case_id="review_case_" + "b" * 64,
        report_id="report_" + "c" * 64,
        connection="primary",
        library="ebooks",
        work_id="item-1",
        destination="title",
        target_fingerprint="d" * 64,
        target_version="e" * 64,
        occurred_at=17,
    )
    first = FieldEffectNoChange.create(**values)
    replay = FieldEffectNoChange.create(**{**values, "occurred_at": 18})
    assert first.event_id == replay.event_id
    assert first.occurred_at != replay.occurred_at
    assert first.origin == "automatic_policy"
    assert first.event_id.startswith("field_effect_no_change_")
    with pytest.raises(FieldEffectError, match="invalid_no_change"):
        FieldEffectNoChange.create(**{**values, "destination": "unknown"})


def test_service_tokens_accept_realistic_opaque_ids_but_remain_bounded() -> None:
    authority = EffectAuthority(
        "human_decision_" + "a" * 64,
        "review_case_" + "b" * 64,
        "c" * 64,
        "fake_service",
        "human",
        "candidate",
        None,
        None,
    )
    consent = EffectConsent.create(
        connection="primary",
        library="books",
        destination="title",
        envelope_identity="a" * 64,
        granted_at=1,
    )

    def create(work_id: object, target_version: object) -> FieldEffectPlan:
        return FieldEffectPlan.create(
            connection="primary",
            library="books",
            work_id=work_id,
            destination="title",
            pre_value="Old",
            desired_value="New",
            target_fingerprint="c" * 64,
            target_version=target_version,
            dependency_digests=(DEPENDENCY,),
            authority=authority,
            consent=consent,
            capability_digest=CAPABILITY,
            created_at=1,
        )

    plan = create("65f12ab90d42c7e81934beef/+opaque", "d" * 64)
    assert plan.work_id == "65f12ab90d42c7e81934beef/+opaque"
    assert plan.target_version == "d" * 64
    for work_id, target_version in (
        ("item\nheader", "v1"),
        ("item\x00suffix", "v1"),
        ("x" * 1025, "v1"),
        ("item-1", "v" * 1025),
        ("item-1", "   "),
        ("item-1", "\ud800"),
        (True, "v1"),
    ):
        with pytest.raises(FieldEffectError):
            create(work_id, target_version)


def test_series_is_one_atomic_destination_and_state_machine_rejects_skips() -> None:
    authority = EffectAuthority(
        "human_decision_" + "a" * 64,
        "review_case_" + "b" * 64,
        "c" * 64,
        "fake_service",
        "human",
        "candidate",
        None,
        None,
    )
    consent = EffectConsent.create(
        connection="primary",
        library="books",
        destination="series",
        envelope_identity="a" * 64,
        granted_at=1,
    )
    plan = FieldEffectPlan.create(
        connection="primary",
        library="books",
        work_id="book-1",
        destination="series",
        pre_value=[{"name": "Old", "sequence": "1"}],
        desired_value=[{"name": "New", "sequence": "2"}],
        target_fingerprint="c" * 64,
        target_version="v1",
        dependency_digests=(DEPENDENCY,),
        authority=authority,
        consent=consent,
        capability_digest=CAPABILITY,
        created_at=1,
    )
    operation = FieldEffectOperation(plan, updated_at=1)
    with pytest.raises(FieldEffectError):
        operation.transition("verified", at=2)

    for member, invalid in (
        ("attempt", True),
        ("updated_at", True),
        ("failure", False),
        ("claim_token", 1),
    ):
        raw = operation.to_data()
        raw[member] = invalid
        with pytest.raises(FieldEffectError):
            FieldEffectOperation.from_json(json.dumps(raw))
    raw = operation.to_data()
    raw["plan"]["created_at"] = True
    with pytest.raises(FieldEffectError):
        FieldEffectOperation.from_json(json.dumps(raw))
    raw = operation.to_data()
    raw["plan"]["consent"]["granted_at"] = True
    with pytest.raises(FieldEffectError):
        FieldEffectOperation.from_json(json.dumps(raw))
