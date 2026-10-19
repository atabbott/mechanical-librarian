from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import (
    DurableBackupReadiness,
    PersistedEffectConsent,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    AudiobookshelfItemReader,
)
from mechanical_librarian.adapters.persistence import field_effect_activity
from mechanical_librarian.adapters.persistence.field_effect_activity import (
    _ACTIVITY_PAGE_CACHE_MAX_BYTES,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    GuardedFieldApplication,
    authority_from_decision,
)
from mechanical_librarian.application.operations import StageTimer, aggregate_incidents
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.activity_records import readable_activity_event
from mechanical_librarian.domain.automatic_decision import (
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
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    comparison_links,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support import audiobookshelf_targets
from tests.support.audiobook_covers import audiobook_cover_environment
from tests.support.audiobook_reports import (
    publish_audiobook_preview,
    synthetic_audiobook_bibliographic_report,
)
from tests.support.audiobookshelf_targets import (
    FieldEffectTargetCase,
    synthetic_audio_file,
)
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.cover_images import synthetic_image
from tests.support.field_effects import (
    CAPABILITY,
    DEPENDENCY,
    _authorize,
    _EffectEnvironment,
    _plan,
    decision,
    envelope,
    metadata_report,
)
from tests.support.field_effects import (
    effects as effects,
)
from tests.support.proposal_evidence import (
    _target_work_candidate,
    _target_work_report,
)
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from tests.unit.test_series_work_applicability import _isbn_series_report

if TYPE_CHECKING:
    # Annotation-only: `TimingCollector` stands in for `TimingRepository`, whose
    # `append` takes exactly this record.
    from mechanical_librarian.application.operations import StageTiming


class TimingCollector:
    def __init__(self) -> None:
        self.values: list[StageTiming] = []

    def append(self, timing) -> None:
        self.values.append(timing)

    def list(self):
        return tuple(self.values)


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
        expected_active_generation=1,
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
        expected_active_generation=1,
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
        search="old",
        event_type="all",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    # A field-effect event names its own decision and values, not a later
    # current report. Search must not make that later title discoverable.
    assert page["total"] == 2
    assert (
        effects.repository.field_effect_activity_page(
            search="readable work",
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )["total"]
        == 0
    )
    assert all(event["report_id"] is None for event in page["events"])
    assert all(
        event["record"]["source_kind"] == "field_effect" for event in page["events"]
    )


def _activity_fixture_operation(
    index: int, occurred_at: int
) -> tuple[FieldEffectOperation, str, str]:
    """Build one canonical planned operation for bounded Activity fixtures."""
    identity = f"{index:064x}"
    authority = EffectAuthority(
        "human_decision_" + identity,
        "review_case_" + "b" * 64,
        "d" * 64,
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
        envelope_identity="e" * 64,
        granted_at=0,
    )
    operation = FieldEffectOperation(
        FieldEffectPlan.create(
            connection="primary",
            library="books",
            work_id=f"book-{index}",
            destination="title",
            pre_value="old",
            desired_value="new",
            target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            authority=authority,
            consent=consent,
            capability_digest=CAPABILITY,
            created_at=occurred_at,
        ),
        updated_at=occurred_at,
    )
    event = {
        "attempt": operation.attempt,
        "decision_id": authority.decision_id,
        "desired_value": operation.plan.desired_value,
        "destination": operation.plan.destination,
        "failure": operation.failure,
        "kind": operation.state,
        "occurred_at": operation.updated_at,
        "origin": authority.origin,
        "plan_id": operation.plan.plan_id,
        "pre_value": operation.plan.pre_value,
        "schema_version": "field-effect-event-v1",
        "sequence": 1,
        "state": operation.state,
    }
    event_id = (
        "effect_event_"
        + hashlib.sha256(
            json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
    )
    return (
        operation,
        event_id,
        json.dumps(
            {**event, "event_id": event_id}, separators=(",", ":"), sort_keys=True
        ),
    )


def test_activity_search_projects_dependency_maps_per_cursor_batch(
    effects: _EffectEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search retains no dependency map beyond its 64-row cursor batch (#787)."""
    for index in range(193):
        operation, event_id, event_document = _activity_fixture_operation(
            index, 1_000_000 + index
        )
        effects.repository._connection.execute(
            "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,work_id,destination,state,attempt,updated_at,document) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                operation.plan.plan_id,
                operation.plan.authority.decision_id,
                operation.plan.connection,
                operation.plan.library,
                operation.plan.work_id,
                operation.plan.destination,
                operation.state,
                operation.attempt,
                operation.updated_at,
                json.dumps(operation.to_data(), separators=(",", ":"), sort_keys=True),
            ),
        )
        effects.repository._connection.execute(
            "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) VALUES(?,?,?,?,?,?)",
            (
                event_id,
                operation.plan.plan_id,
                1,
                "planned",
                operation.updated_at,
                event_document,
            ),
        )
    effects.repository._connection.commit()
    projected: list[tuple[int, bool]] = []
    projection = effects.repository._field_effect_activity
    original = projection._project_activity_rows

    def track_projection(rows: list[tuple[object, ...]], **kwargs: object) -> object:
        projected.append((len(rows), kwargs["searched"] is True))
        return original(rows, **kwargs)

    monkeypatch.setattr(projection, "_project_activity_rows", track_projection)
    page = effects.repository.field_effect_activity_page(
        search="book-",
        event_type="review",
        occurred_after=0,
        page=1_000_000,
        page_size=25,
    )
    assert len(projected) >= 4
    assert all(searched and 1 <= size <= 64 for size, searched in projected)
    assert page["total"] == 193
    assert page["page"] == page["page_count"] == 8
    assert len(page["events"]) == 18
    assert [event["occurred_at"] for event in page["events"]] == list(
        range(1_000_017, 1_000_000 - 1, -1)
    )
    final_page = effects.repository.field_effect_activity_page(
        search="book-", event_type="review", occurred_after=0, page=8, page_size=25
    )
    assert [event["event_id"] for event in page["events"]] == [
        event["event_id"] for event in final_page["events"]
    ]


def test_activity_page_bounds_filters_and_clamps_the_append_only_ledger(
    effects: _EffectEnvironment,
) -> None:
    for index in range(101):
        occurred_at = 1_000_000 + index
        operation, event_id, event_document = _activity_fixture_operation(
            index, occurred_at
        )
        effects.repository._connection.execute(
            "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,work_id,"
            "destination,state,attempt,updated_at,document) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                operation.plan.plan_id,
                operation.plan.authority.decision_id,
                operation.plan.connection,
                operation.plan.library,
                operation.plan.work_id,
                operation.plan.destination,
                operation.state,
                operation.attempt,
                operation.updated_at,
                json.dumps(operation.to_data(), separators=(",", ":"), sort_keys=True),
            ),
        )
        effects.repository._connection.execute(
            "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) "
            "VALUES(?,?,?,?,?,?)",
            (
                event_id,
                operation.plan.plan_id,
                1,
                "planned",
                occurred_at,
                event_document,
            ),
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


def test_activity_page_cache_keeps_only_an_exact_current_reader_projection(
    effects: _EffectEnvironment,
) -> None:
    _plan(effects, suffix="a")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    query = dict(search=None, event_type="all", occurred_after=0, page=1, page_size=25)
    statements: list[str] = []
    try:
        first = reader.field_effect_activity_page(**query)
        assert {
            key: value.to_json()
            for key, value in first.pop("validated_reports").items()
        } == first["report_documents"]
        reader._connection.set_trace_callback(statements.append)
        with patch.object(
            reader._field_effect_activity,
            "_activity_database_identity",
            wraps=reader._field_effect_activity._activity_database_identity,
        ) as identity:
            second = reader.field_effect_activity_page(**query)
        reader._connection.set_trace_callback(None)
        assert "validated_reports" not in second
        assert all(
            "validated_reports" not in entry[4]
            for entry in reader._field_effect_activity._activity_page_cache.values()
        )
        assert second == first
        assert statements == ["PRAGMA data_version"]
        assert identity.call_count == 1

        # A caller receives a copy: its owner-readable shaping cannot corrupt
        # the reader's retained immutable projection.
        second["events"].clear()
        assert reader.field_effect_activity_page(**query) == first

        _plan(effects, suffix="b")
        refreshed = reader.field_effect_activity_page(**query)
        assert cast(int, refreshed["total"]) > cast(int, first["total"])
    finally:
        reader._connection.set_trace_callback(None)
        reader.close()


def test_activity_page_cache_rejects_out_of_band_event_byte_mutation(
    effects: _EffectEnvironment,
) -> None:
    _plan(effects, suffix="f")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    query = dict(search=None, event_type="all", occurred_after=0, page=1, page_size=25)
    projection = reader._field_effect_activity
    try:
        reader.field_effect_activity_page(**query)
        document = effects.repository._connection.execute(
            "SELECT document FROM field_effect_events ORDER BY occurred_at,event_id LIMIT 1"
        ).fetchone()[0]
        encoded = document.encode("utf-8")
        database = effects.database_path.read_bytes()
        offset = database.find(encoded)
        assert offset >= 0
        assert database.find(encoded, offset + 1) == -1
        revision = projection._activity_projection_revision()
        identity = projection._activity_database_identity()

        descriptor = os.open(effects.database_path, os.O_WRONLY)
        try:
            assert os.pwrite(descriptor, b"!", offset) == 1
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        assert projection._activity_projection_revision() == revision
        assert projection._activity_database_identity() != identity
        with (
            patch.object(
                projection,
                "_field_effect_activity_page_uncached",
                side_effect=AssertionError(
                    "hostile bytes must not reach the warm reader"
                ),
            ),
            pytest.raises(FieldEffectError, match="^persistence$"),
        ):
            reader.field_effect_activity_page(**query)
    finally:
        reader.close()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux authority bindings use hard-link aliases",
)
def test_activity_page_cache_ignores_authority_alias_link_churn(
    effects: _EffectEnvironment,
) -> None:
    _plan(effects, suffix="a")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    query = dict(search=None, event_type="all", occurred_after=0, page=1, page_size=25)
    projection = reader._field_effect_activity
    original_page = projection._field_effect_activity_page_uncached
    page_calls = [0]

    def counted_page(**values: object):
        page_calls[0] += 1
        return original_page(**values)

    try:
        with patch.object(
            projection, "_field_effect_activity_page_uncached", counted_page
        ):
            first = reader.field_effect_activity_page(**query)
            assert {
                key: value.to_json()
                for key, value in first.pop("validated_reports").items()
            } == first["report_documents"]
            revision = projection._activity_projection_revision()
            identity = projection._activity_database_identity()
            before = os.stat(effects.database_path, follow_symlinks=False)

            derived = open_repository(
                effects.database_path,
                create=False,
                authority=effects.repository.authority,
                cross_thread=True,
            )
            try:
                assert (
                    os.stat(effects.database_path, follow_symlinks=False).st_nlink
                    == before.st_nlink + 1
                )
            finally:
                derived.close()

            after = os.stat(effects.database_path, follow_symlinks=False)
            assert after.st_nlink == before.st_nlink
            assert after.st_ctime_ns != before.st_ctime_ns
            assert projection._activity_projection_revision() == revision
            assert projection._activity_database_identity() == identity
            assert reader.field_effect_activity_page(**query) == first
        assert page_calls == [1]
    finally:
        reader.close()


def test_activity_page_cache_refreshes_when_a_commit_races_its_file_sample(
    effects: _EffectEnvironment,
) -> None:
    _plan(effects, suffix="a")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    query = dict(search=None, event_type="all", occurred_after=0, page=1, page_size=25)
    projection = reader._field_effect_activity
    first = reader.field_effect_activity_page(**query)
    original_identity = projection._activity_database_identity
    committed = False

    def commit_before_identity() -> tuple[int, int, int, int]:
        nonlocal committed
        if not committed:
            committed = True
            _plan(effects, suffix="b")
        return original_identity()

    try:
        with patch.object(
            projection, "_activity_database_identity", commit_before_identity
        ):
            refreshed = reader.field_effect_activity_page(**query)
    finally:
        reader.close()

    assert committed is True
    assert cast(int, refreshed["total"]) > cast(int, first["total"])


def test_activity_page_cache_reuses_a_rolling_range_until_an_event_ages_out(
    effects: _EffectEnvironment,
) -> None:
    _plan(effects, suffix="a")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    projection = reader._field_effect_activity
    original = projection._field_effect_activity_page_uncached
    projection_calls = [0]

    def counted_projection(**values):
        projection_calls[0] += 1
        return original(**values)

    query = dict(search=None, event_type="review", page=1, page_size=25)
    with patch.object(
        projection, "_field_effect_activity_page_uncached", counted_projection
    ):
        try:
            first = reader.field_effect_activity_page(occurred_after=0, **query)
            assert {
                key: value.to_json()
                for key, value in first.pop("validated_reports").items()
            } == first["report_documents"]
            oldest = min(
                cast(int, event["occurred_at"])
                for event in cast("list[dict[str, object]]", first["events"])
            )
            assert reader.field_effect_activity_page(occurred_after=1, **query) == first
            expired = reader.field_effect_activity_page(
                occurred_after=oldest + 1, **query
            )
        finally:
            reader.close()

    assert projection_calls == [2]
    assert expired != first


def test_activity_page_cache_declines_valid_reports_over_its_byte_budget(
    effects: _EffectEnvironment,
) -> None:
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    projection = reader._field_effect_activity

    def large_report(index: int) -> MetadataReport:
        local = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Large",
            canonical="Large",
            excerpt="Large",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        candidates = []
        for candidate_index in range(5):
            candidate = f"candidate-{index}-{candidate_index}"
            bare = ProviderClaim.create(
                provider="catalogue",
                candidate=candidate,
                kind=ClaimKind.DESCRIPTION,
                value="📚" * MAX_DESCRIPTION_CHARACTERS,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                raw_order=0,
            )
            claim = ProviderClaim.create(
                provider="catalogue",
                candidate=candidate,
                kind=bare.kind,
                value=bare.value,
                state=bare.state,
                scope=bare.scope,
                applicability=bare.applicability,
                raw_order=bare.raw_order,
                links=comparison_links(bare, (local,)),
            )
            candidates.append(ProviderCandidate(candidate, candidate, (claim,)))
        return MetadataReport.create(
            target=TargetFacts(
                "primary",
                "books",
                f"item-{index}",
                default_target_fields("Large"),
            ),
            file=FileFacts(0, "a" * 64, f"{index:064d}"),
            local_claims=(local,),
            local_reasons=("opening_matter_title",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    tuple(candidates),
                ),
            ),
        )

    reports = [large_report(index) for index in range(7)]
    documents = {report.report_id: report.to_json() for report in reports}
    assert sum(len(value) for value in documents.values()) <= (
        _ACTIVITY_PAGE_CACHE_MAX_BYTES
    )
    assert sum(len(value.encode("utf-8")) for value in documents.values()) > (
        _ACTIVITY_PAGE_CACHE_MAX_BYTES
    )
    assert all(
        MetadataReport.from_json(document) == report
        for report, document in zip(reports, documents.values(), strict=True)
    )
    result: dict[str, object] = {
        "current_waiting": None,
        "events": [],
        "page": 1,
        "page_count": 1,
        "page_size": 100,
        "report_documents": documents,
        "total": 0,
    }
    revision = (0, 0, 0, 0, 0, "")
    projection_calls = [0]
    reader.field_effect_activity_page(
        search=None,
        event_type="all",
        occurred_after=0,
        page=1,
        page_size=25,
    )

    def large_page(**_query: object):
        projection_calls[0] += 1
        return result, revision, None, projection._activity_database_identity()

    with patch.object(projection, "_field_effect_activity_page_uncached", large_page):
        try:
            query = dict(
                search=None,
                event_type="all",
                occurred_after=0,
                page=1,
                page_size=100,
            )
            assert reader.field_effect_activity_page(**query) == result
            assert reader.field_effect_activity_page(**query) == result
        finally:
            reader.close()

    assert projection_calls == [2]
    assert list(projection._activity_page_cache) == [(None, "all", 1, 25)]
    assert projection._activity_page_cache_bytes == 0

    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    projection = reader._field_effect_activity
    first_documents = dict(list(documents.items())[:4])
    second_documents = dict(list(documents.items())[4:])
    first_bytes = sum(len(value.encode("utf-8")) for value in first_documents.values())
    second_bytes = sum(
        len(value.encode("utf-8")) for value in second_documents.values()
    )
    assert first_bytes <= _ACTIVITY_PAGE_CACHE_MAX_BYTES
    assert second_bytes <= _ACTIVITY_PAGE_CACHE_MAX_BYTES
    assert first_bytes + second_bytes > _ACTIVITY_PAGE_CACHE_MAX_BYTES
    page_results = {
        25: {**result, "page_size": 25, "report_documents": first_documents},
        50: {**result, "page_size": 50, "report_documents": second_documents},
    }
    bounded_calls: list[int] = []

    def bounded_page(**query: object):
        page_size = cast(int, query["page_size"])
        bounded_calls.append(page_size)
        return (
            page_results[page_size],
            revision,
            None,
            projection._activity_database_identity(),
        )

    with (
        patch.object(projection, "_field_effect_activity_page_uncached", bounded_page),
        patch.object(
            projection, "_activity_projection_revision", return_value=revision
        ),
    ):
        try:
            for page_size in (25, 25, 50, 25):
                assert (
                    reader.field_effect_activity_page(
                        search=None,
                        event_type="all",
                        occurred_after=0,
                        page=1,
                        page_size=page_size,
                    )
                    == page_results[page_size]
                )
        finally:
            reader.close()

    assert bounded_calls == [25, 50, 25]
    assert list(projection._activity_page_cache) == [(None, "all", 1, 25)]
    assert projection._activity_page_cache_bytes == first_bytes


def test_activity_example_selector_returns_one_current_published_candidate(
    effects: _EffectEnvironment,
) -> None:
    from tests.unit.test_activity_end_to_end import _automatic_candidate_decision

    _automatic_candidate_decision(effects)

    document = effects.repository.pending_field_effect_activity_example_document()

    assert document is not None
    selected = AutomaticDecision.from_json(document)
    snapshot = PersistedFieldEffectAuthority(effects.repository).resolve(selected)
    assert snapshot.review_case_id == selected.review_case_id
    assert snapshot.destination == selected.destination


def test_activity_backup_readiness_rechecks_each_exact_database_revision(
    effects: _EffectEnvironment,
) -> None:
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    statements: list[str] = []
    reader._connection.set_trace_callback(statements.append)
    try:
        assert reader.field_effect_backup_ready() is True
        assert reader.field_effect_backup_ready() is True
        assert statements.count("PRAGMA quick_check") == 1

        effects.repository.set_field_effect_paused(True, effects.clock())
        assert reader.field_effect_backup_ready() is True
        assert statements.count("PRAGMA quick_check") == 2

        reader.set_field_effect_paused(False, effects.clock())
        assert reader.field_effect_backup_ready() is True
        assert statements.count("PRAGMA quick_check") == 3
    finally:
        reader._connection.set_trace_callback(None)
        reader.close()


def test_write_preclaim_requires_fresh_backup_readiness(
    effects: _EffectEnvironment,
) -> None:
    checks: list[bool] = []

    class Repository:
        def field_effect_backup_ready(self, *, fresh: bool = False) -> bool:
            checks.append(fresh)
            return True

    assert DurableBackupReadiness(Repository()).ready(_plan(effects, suffix="0").plan)
    assert checks == [True]


def test_fresh_backup_readiness_rejects_corruption_after_a_cached_success(
    effects: _EffectEnvironment,
) -> None:
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    with effects.database_path.open("r+b") as database:
        header = database.read(1)
    try:
        assert reader.field_effect_backup_ready() is True
        with effects.database_path.open("r+b") as database:
            database.write(b"X")
        assert reader.field_effect_backup_ready() is True
        assert reader.field_effect_backup_ready(fresh=True) is False
    finally:
        with effects.database_path.open("r+b") as database:
            database.write(header)
        reader.close()


def test_activity_snapshot_holds_and_releases_one_exact_read_transaction(
    effects: _EffectEnvironment,
) -> None:
    _plan(effects, suffix="5")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    reader.configure_activity_page_reader()
    statements: list[str] = []
    reader._connection.set_trace_callback(statements.append)

    def projection() -> dict[str, object]:
        assert reader._connection.in_transaction
        summary = reader.field_effect_activity_summary()
        page = reader.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        return {"summary": summary, "page": page}

    def fail_projection() -> dict[str, object]:
        raise RuntimeError("projection failure")

    try:
        result = reader.field_effect_activity_snapshot(projection)
        assert result["summary"]
        assert result["page"]
        assert not reader._connection.in_transaction

        with pytest.raises(RuntimeError, match="projection failure"):
            reader.field_effect_activity_snapshot(fail_projection)
        assert not reader._connection.in_transaction
    finally:
        reader._connection.set_trace_callback(None)
        reader.close()

    assert statements.count("BEGIN") == 2
    assert statements.count("ROLLBACK") == 2


def test_activity_page_cache_invalidates_an_in_place_policy_transition_update(
    effects: _EffectEnvironment,
) -> None:
    builtin = MetadataPolicyProfile.builtin()
    replacement = MetadataPolicyProfile.create(
        "Replacement", dict(builtin.options), dict(builtin.destinations)
    )
    effects.repository.create_metadata_policy_draft(replacement.canonical_json)
    resolution = resolve_policy(replacement, "books")
    effects.repository.activate_metadata_policy(
        replacement.policy_id,
        builtin.policy_id,
        (("books", resolution.resolved_id, resolution.canonical_json),),
        2,
        expected_active_generation=1,
    )
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    query = dict(
        search=None, event_type="policy", occurred_after=0, page=1, page_size=25
    )
    projection_calls = [0]
    projection = reader._field_effect_activity
    original = projection._field_effect_activity_page_uncached

    def counted_projection(**values):
        projection_calls[0] += 1
        return original(**values)

    with patch.object(
        projection, "_field_effect_activity_page_uncached", counted_projection
    ):
        try:
            first = reader.field_effect_activity_page(**query)
            assert {
                key: value.to_json()
                for key, value in first.pop("validated_reports").items()
            } == first["report_documents"]
            assert reader.field_effect_activity_page(**query) == first
            effects.repository._connection.execute(
                "UPDATE metadata_policy_transitions SET updated_at=updated_at+1"
            )
            effects.repository._connection.commit()
            refreshed = reader.field_effect_activity_page(**query)
        finally:
            reader.close()

    assert projection_calls == [2]
    assert refreshed != first


def test_activity_page_cache_revalidates_an_in_place_malformed_event(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="2")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    query = dict(search=None, event_type="all", occurred_after=0, page=1, page_size=25)
    try:
        assert reader.field_effect_activity_page(**query)["events"]
        effects.repository._connection.execute(
            "DROP TRIGGER field_effect_events_no_update"
        )
        effects.repository._connection.execute(
            "UPDATE field_effect_events SET document=? WHERE plan_id=? AND sequence=1",
            ("{not-json", operation.plan.plan_id),
        )
        effects.repository._connection.commit()
        with pytest.raises(FieldEffectError, match="^persistence$"):
            reader.field_effect_activity_page(**query)
    finally:
        reader.close()


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


def test_activity_page_ignores_a_corrupt_later_current_report_for_field_event(
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

    page = effects.repository.field_effect_activity_page(
        search=None,
        event_type="all",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    assert any(event["destination"] == "title" for event in page["events"])
    assert not effects.repository._connection.in_transaction


def test_activity_page_does_not_substitute_a_later_report_for_current_waiting(
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

    page = effects.repository.field_effect_activity_page(
        search=None,
        event_type="all",
        occurred_after=2_000_000,
        page=1,
        page_size=25,
    )
    assert page["current_waiting"]["work_label"] == "Recorded work"
    assert not effects.repository._connection.in_transaction


def test_activity_page_rejects_an_earlier_valid_field_event_that_disagrees_with_columns(
    effects: _EffectEnvironment,
) -> None:
    plan = _plan(effects, suffix="1")
    assert effects.service.apply(plan.plan.plan_id).state == "verified"
    row = effects.repository._connection.execute(
        "SELECT event_id,document FROM field_effect_events WHERE plan_id=? AND sequence=1",
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


def test_activity_rejects_a_canonical_event_with_other_operation_authority(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="7")
    effects.service.apply(operation.plan.plan_id)
    rows = effects.repository._connection.execute(
        "SELECT event_id,document FROM field_effect_events WHERE plan_id=? "
        "ORDER BY sequence",
        (operation.plan.plan_id,),
    ).fetchall()
    original_event_id, original_document = rows[0]
    latest_event_id = rows[-1][0]
    corrupted = json.loads(original_document)
    corrupted["decision_id"] = "human_decision_" + "f" * 64
    corrupted.pop("event_id")
    replacement_event_id = (
        "effect_event_"
        + hashlib.sha256(
            json.dumps(corrupted, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
    )
    replacement_document = json.dumps(
        {**corrupted, "event_id": replacement_event_id},
        separators=(",", ":"),
        sort_keys=True,
    )
    effects.repository._connection.execute("DROP TRIGGER field_effect_events_no_update")
    effects.repository._connection.execute(
        "UPDATE field_effect_events SET event_id=?,document=? WHERE event_id=?",
        (replacement_event_id, replacement_document, original_event_id),
    )
    effects.repository._connection.commit()

    with pytest.raises(FieldEffectError, match="^invalid_event$"):
        effects.repository.field_effect_event_documents(operation.plan.plan_id)
    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_transitions(latest_event_id)


@pytest.mark.parametrize(
    "failure", (FieldEffectError("invalid_event"), sqlite3.OperationalError("locked"))
)
def test_activity_transitions_rolls_back_canonical_reader_failures(
    effects: _EffectEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    operation = _plan(effects, suffix="8")

    def fail(_: str) -> tuple[str, ...]:
        raise failure

    monkeypatch.setattr(
        effects.repository._field_effect_activity, "_field_effect_event_documents", fail
    )
    event_id = effects.repository._connection.execute(
        "SELECT event_id FROM field_effect_events WHERE plan_id=?",
        (operation.plan.plan_id,),
    ).fetchone()[0]
    with pytest.raises(FieldEffectError, match="^persistence$"):
        effects.repository.field_effect_activity_transitions(event_id)
    assert not effects.repository._connection.in_transaction


def test_activity_transitions_nested_read_scope_keeps_its_outer_transaction(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="9")
    event_id = effects.repository._connection.execute(
        "SELECT event_id FROM field_effect_events WHERE plan_id=?",
        (operation.plan.plan_id,),
    ).fetchone()[0]
    outer = effects.repository._field_effect_activity._transaction("outer")
    outer.begin()
    try:
        assert ["planned"] == [
            event["state"]
            for event in effects.repository.field_effect_activity_transitions(event_id)
        ]
        assert effects.repository._connection.in_transaction
    finally:
        outer.rollback()


def test_activity_transitions_hold_one_snapshot_across_a_lifecycle_append(
    effects: _EffectEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = _plan(effects, suffix="a")
    event_id = effects.repository._connection.execute(
        "SELECT event_id FROM field_effect_events WHERE plan_id=?",
        (operation.plan.plan_id,),
    ).fetchone()[0]
    reader = effects.repository._field_effects
    original = reader.get_field_effect
    writer_started = threading.Event()
    writer_finished = threading.Event()
    writer_failures: list[Exception] = []

    def event_document(
        current: FieldEffectOperation, kind: str, sequence: int
    ) -> tuple[str, str]:
        event = {
            "attempt": current.attempt,
            "decision_id": current.plan.authority.decision_id,
            "desired_value": current.plan.desired_value,
            "destination": current.plan.destination,
            "failure": current.failure,
            "kind": kind,
            "occurred_at": current.updated_at,
            "origin": current.plan.authority.origin,
            "plan_id": current.plan.plan_id,
            "pre_value": current.plan.pre_value,
            "schema_version": "field-effect-event-v1",
            "sequence": sequence,
            "state": kind,
        }
        event_id = (
            "effect_event_"
            + hashlib.sha256(
                json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
        )
        return event_id, json.dumps(
            {**event, "event_id": event_id}, separators=(",", ":"), sort_keys=True
        )

    claimed = operation.transition(
        "claimed", at=operation.updated_at + 1, claim_token="a" * 64
    )
    applying = claimed.transition("applying", at=claimed.updated_at + 1)
    verified = applying.transition("verified", at=applying.updated_at + 1)

    def append_lifecycle() -> None:
        connection = sqlite3.connect(effects.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            writer_started.set()
            connection.execute(
                "UPDATE field_effects SET state=?,attempt=?,updated_at=?,document=? WHERE plan_id=?",
                (
                    verified.state,
                    verified.attempt,
                    verified.updated_at,
                    json.dumps(
                        verified.to_data(), separators=(",", ":"), sort_keys=True
                    ),
                    verified.plan.plan_id,
                ),
            )
            for sequence, current in enumerate((claimed, applying, verified), 2):
                next_event_id, document = event_document(
                    current, current.state, sequence
                )
                connection.execute(
                    "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) VALUES(?,?,?,?,?,?)",
                    (
                        next_event_id,
                        current.plan.plan_id,
                        sequence,
                        current.state,
                        current.updated_at,
                        document,
                    ),
                )
            connection.commit()
        except Exception as error:  # noqa: BLE001 - test worker preserves its failure
            writer_failures.append(error)
        finally:
            connection.close()
            writer_finished.set()

    writer = threading.Thread(target=append_lifecycle)

    def interleave(plan_id: str) -> FieldEffectOperation:
        persisted = original(plan_id)
        writer.start()
        assert writer_started.wait(1)
        return persisted

    monkeypatch.setattr(reader, "get_field_effect", interleave)
    transitions = effects.repository.field_effect_activity_transitions(event_id)
    monkeypatch.setattr(reader, "get_field_effect", original)
    writer.join(5)
    assert not writer.is_alive()
    assert writer_finished.is_set()
    assert writer_failures == []
    assert [(1, "planned")] == [
        (event["sequence"], event["state"]) for event in transitions
    ]
    assert ["planned", "claimed", "applying", "verified"] == [
        json.loads(document)["state"]
        for document in effects.repository.field_effect_event_documents(
            operation.plan.plan_id
        )
    ]


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
            "report_document_digest": effects.repository._connection.execute(
                "SELECT document_digest FROM metadata_reports WHERE report_id=?",
                (case.report_id,),
            ).fetchone()[0],
            "replay_digest": "8" * 64,
            "review_case_id": case.review_case_id,
            "schema_version": "cover-consideration-v4",
            "supersedes_id": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    effects.repository._connection.execute(
        "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document,currentness_document) VALUES(?,?,?,?,?,?,?,?,'null')",
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

    The private binding owns an exclusive `flock` on the database's parent
    directory (`adapters/persistence/binding.py`), so a second independent
    repository beside the fixture's own is refused on Linux. Give every extra
    handle its own owner-only directory so its root authority can be admitted
    independently.
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
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
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
        "narrators": ["N"],
        "explicit": True,
        "tags": ["T"],
        "cover": "cover_" + "a" * 64,
    }
    assert SUPPORTED_DESTINATIONS == set(values)
    # Every supported shape reaches the ordinary guarded plan constructor.
    assert "narrators" in SUPPORTED_DESTINATIONS
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


@pytest.mark.parametrize(
    "guard", (None, "backup", "target", "readback", "consent", "authority")
)
@pytest.mark.parametrize("destination", ("title", "isbn", "audiobook_title"))
def test_isbn_uses_persisted_guarded_effect_authority(
    effects: _EffectEnvironment,
    guard: str | None,
    destination: str,
) -> None:
    from tests.support.audiobook_reports import publish_audiobook_preview
    from tests.support.proposal_evidence import _image_unavailable_isbn_report

    audiobook_title = destination == "audiobook_title"
    if audiobook_title:
        from mechanical_librarian.domain.metadata_report import (
            TargetDestination,
            TargetField,
            TargetFieldState,
        )
        from tests.support.audiobook_reports import asserted_asin_report

        destination = "title"
        report = asserted_asin_report(provider_duration="58200.000")
        report = MetadataReport.create(
            target=replace(
                report.target,
                fields=tuple(
                    TargetField(
                        field.destination,
                        TargetFieldState.VALUE,
                        "Project Hail Mary (Unabridged)",
                    )
                    if field.destination is TargetDestination.TITLE
                    else field
                    for field in report.target.fields
                ),
            ),
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            audio=report.audio,
        )
        case_id, _ = publish_audiobook_preview(
            effects.repository, report, ("title_layout",)
        )
        effects.repository.persist_shadow_outcome(case_id, 4)
    elif destination == "isbn":
        case_id, _ = publish_audiobook_preview(
            effects.repository,
            _image_unavailable_isbn_report(),
            ("isbn_form",),
            selected_options={"isbn_form": "prefer_isbn13"},
        )
        effects.repository.persist_shadow_outcome(case_id, 4)
    else:
        from tests.support.evidence_led_titles import (
            isbn_title_report,
            publish_title_case,
        )

        publish_title_case(effects.repository, isbn_title_report())
    document = effects.repository._connection.execute(
        "SELECT document FROM automatic_decisions WHERE destination=?", (destination,)
    ).fetchone()[0]
    decision = AutomaticDecision.from_json(document)
    authority = PersistedFieldEffectAuthority(effects.repository)
    snapshot = authority.resolve(decision)
    service = effects.persisted_service(effects.repository, effects.target, authority)
    with pytest.raises(GuardedApplicationError, match="consent"):
        service.plan(
            decision,
            connection=snapshot.connection,
            library=snapshot.library,
            work_id=snapshot.work_id,
            destination=destination,
            pre_value=snapshot.pre_value,
            desired_value=snapshot.decision_value,
            target_fingerprint=snapshot.target_fingerprint,
            target_version=snapshot.target_version,
            dependency_digests=snapshot.dependency_digests,
            capability_digest=CAPABILITY,
        )
    assert not effects.target._values
    effects.repository.grant_field_effect_consents(
        snapshot.connection, (snapshot.library,), (destination,), 5
    )
    service = effects.persisted_service(effects.repository, effects.target, authority)
    result = service.plan(
        decision,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination=destination,
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert result is not None
    assert result.plan.authority.origin == "automatic_policy"
    effects.target.seed(result.plan)
    if guard == "backup":
        effects.backup.ready_value = False
    elif guard == "target":
        effects.target.drift(result.plan, "9780140328721")
    elif guard == "readback":
        effects.target.fail_read_after_apply_for.add(result.plan.plan_id)
    elif guard == "consent":
        effects.repository.revoke_field_effect_consent(
            result.plan.consent.consent_id, effects.clock()
        )
    elif guard == "authority":
        effects.repository.revoke_field_effect_authority(
            decision.decision_id, effects.clock()
        )
    before = dict(effects.target._values)
    applied = service.apply(result.plan.plan_id)
    if guard in {"consent", "authority"}:
        assert (applied.state, applied.failure) == (
            "superseded",
            "consent_revoked" if guard == "consent" else "authority_superseded",
        )
        assert effects.target._values == before
        return
    if guard in {"backup", "target"}:
        assert (applied.state, applied.failure) == (
            "stale",
            "target_changed" if guard == "target" else guard,
        )
        assert effects.target._values == before
        return
    if guard == "readback":
        assert (applied.state, applied.failure) == ("reconciling", "target_failure")
        assert effects.target._values != before
        return
    assert applied.state == "verified"
    assert service.apply(result.plan.plan_id).state == "verified"
    changed = {
        key for key, value in effects.target._values.items() if before.get(key) != value
    }
    assert changed == {
        (snapshot.connection, snapshot.library, snapshot.work_id, destination)
    }
    assert effects.target._values[next(iter(changed))] == (
        "Project Hail Mary"
        if audiobook_title
        else "9780306406157"
        if destination == "isbn"
        else "Example Title"
    )


def test_an_activated_audiobook_tranche_plans_through_the_same_guarded_path(
    effects: _EffectEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An F46 decision reaches the planner exactly as an ebook's does.

    ADR-0071 already extends guarded application to book-media destinations,
    and an `AutomaticDecision` carries no `application` marker, so activating a
    tranche is a widening of write authority under that existing envelope
    rather than a new path. Consent, plan and effect are unchanged (#967).
    """
    identity = audiobook_decision_qualification.F46_ASIN_IDENTITY
    category, option = identity.split(":", 1)
    record = "7" * 64
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        frozenset({identity}),
    )
    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_QUALIFICATION_MANIFEST_SHA256", record
    )
    automatic = AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="asin",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category=category,
        policy_option=option,
        qualification_manifest_sha256=record,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        bundle_id=None,
        occurred_at=1,
    )
    effects.authority.automatic_values[automatic.decision_id] = envelope(
        "asin", "B08G9PRS1K"
    )
    _authorize(effects, automatic, work_id="recording-1")
    result = effects.service.plan(
        automatic,
        connection="primary",
        library="books",
        work_id="recording-1",
        destination="asin",
        pre_value=None,
        desired_value=envelope("asin", "B08G9PRS1K"),
        target_fingerprint="e" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert result is not None
    assert result.plan.authority.origin == "automatic_policy"
    effects.target.seed(result.plan)
    assert effects.service.apply(result.plan.plan_id).state == "verified"


@pytest.mark.parametrize(
    "prior_digest",
    (
        "6535df8f7c4cebefa62f9c38ab01823a377c0c7048fca20b81aaf250b498b7ff",
        "7aeaca9b020022b9ab385a9baf249d1b63dc7ffcfcc4a20735d6f6480b5b68e7",
        "12607559ec04da639e36fe0afe8e9e01eefb59c6851555a43175fe3c07323dc5",
        "99ba8426ee53e4d31e52ecf0f65a0fce4eb237d3e5acef42e4ee17217b57dae9",
        "4ede15a2aa84e8726ea2dff4e4f9f0d04b27a70276ed53c400c2de5f7619d445",
        "091cdf7f08f60e9e4e1860ff574a9c04c405c914d498560e9802294b69c53a30",
        "70563a47e768fe51cb911511ca67849893e4495266c84bb2e9fc7f6165ed2879",
        "4a89dfc24a27bbe2a548a9b0c5ad2793cf2da1a2b0445f9dd1ad2f9323822bc9",
        "19939b85acb471fb17c076a44c6a0dd173f6c32e5a951986b9e65060458cfd8a",
    ),
)
@pytest.mark.parametrize(
    "category, option",
    (
        ("tags", "fill_empty"),
        ("series", "primary_narrative"),
        ("series", "source_order"),
        ("series", "all_qualified"),
    ),
)
def test_legacy_rich_decision_stays_readable_without_effect_authority(
    effects: _EffectEnvironment,
    prior_digest: str,
    category: str,
    option: str,
) -> None:
    current = next(
        decision
        for decision in _automatic_decisions(
            effects,
            _isbn_series_report()
            if category == "series"
            else _target_work_report(
                (
                    (
                        "catalogue",
                        "hardcover",
                        (_target_work_candidate("catalogue", "edition-1", "work-1"),),
                        True,
                    ),
                )
            ),
            category,
            option,
            qualified=True,
        )
        if decision.destination == category and decision.outcome == "candidate"
    )
    assert current.qualification_manifest_sha256 != prior_digest
    assert authority_from_decision(current) is not None
    legacy_core = {
        **current._core(),
        "qualification_manifest_sha256": prior_digest,
    }

    def digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()

    legacy = AutomaticDecision(
        decision_id="automatic_decision_"
        + digest({**legacy_core, "occurred_at": current.occurred_at}),
        **legacy_core,
        idempotency_digest=digest(legacy_core),
        occurred_at=current.occurred_at,
    )
    assert legacy == AutomaticDecision.from_json(legacy.to_json())
    assert authority_from_decision(legacy) is None

    connection = effects.repository._connection
    connection.execute("DROP TRIGGER automatic_decisions_no_update")
    connection.execute("DROP TRIGGER automatic_decisions_no_delete")
    connection.execute(
        "DELETE FROM automatic_decisions WHERE decision_id=?",
        (current.decision_id,),
    )
    connection.execute(
        "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,"
        "idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
        (
            legacy.decision_id,
            legacy.review_case_id,
            legacy.destination,
            legacy.idempotency_digest,
            legacy.occurred_at,
            legacy.to_json(),
        ),
    )
    connection.execute(
        "CREATE TRIGGER automatic_decisions_no_update BEFORE UPDATE ON "
        "automatic_decisions BEGIN SELECT RAISE(ABORT, 'automatic decisions are "
        "append-only'); END"
    )
    connection.execute(
        "CREATE TRIGGER automatic_decisions_no_delete BEFORE DELETE ON "
        "automatic_decisions BEGIN SELECT RAISE(ABORT, 'automatic decisions are "
        "append-only'); END"
    )
    connection.commit()
    assert legacy.to_json() in effects.repository.get_review_case_decisions(
        legacy.review_case_id
    )
    snapshot = effects.repository.read_review_library(None, 20, 0, None)
    assert any(legacy.to_json() in row[2] for row in snapshot.rows)

    source = effects.repository.field_effect_authority_source(legacy)
    consent = effects.repository.grant_field_effect_consents(
        cast(str, source["connection"]),
        (cast(str, source["library"]),),
        (legacy.destination,),
        5,
    )[0]
    authority = EffectAuthority(
        legacy.decision_id,
        legacy.review_case_id,
        legacy.target_fingerprint,
        "persisted_review_case",
        "automatic_policy",
        "candidate",
        legacy.resolved_policy_id,
        legacy.qualification_manifest_sha256,
    )
    plan = FieldEffectPlan.create(
        connection=cast(str, source["connection"]),
        library=cast(str, source["library"]),
        work_id=cast(str, source["work_id"]),
        destination=legacy.destination,
        pre_value=source["pre_value"],
        desired_value=project_decision_value(
            legacy.destination, source["decision_value"]
        ),
        target_fingerprint=legacy.target_fingerprint,
        target_version=cast(str, source["target_version"]),
        dependency_digests=cast(tuple[str, ...], source["dependency_digests"]),
        authority=authority,
        consent=consent,
        capability_digest=CAPABILITY,
        created_at=5,
    )
    effects.repository.record_field_effect(FieldEffectOperation(plan, updated_at=5))

    assert not effects.repository.field_effect_authority_is_current(authority)
    with pytest.raises(FieldEffectError, match="^authority$"):
        effects.repository.claim_field_effect(
            plan.plan_id,
            "planned",
            "a" * 64,
            6,
        )
    assert effects.repository.get_field_effect(plan.plan_id).state == "planned"


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


@pytest.mark.parametrize("added", ("narrators", "explicit"))
def test_a_new_write_destination_lapses_every_existing_library_consent(
    effects: _EffectEnvironment,
    added: str,
) -> None:
    """A newly supported field lapses prior grants; no field inherits consent."""
    prior_destinations = frozenset(SUPPORTED_DESTINATIONS - {added})
    assert added in SUPPORTED_DESTINATIONS
    with patch.object(
        field_effect_activity, "SUPPORTED_DESTINATIONS", prior_destinations
    ):
        granted = effects.repository.grant_field_effect_consents(
            "primary", ("books",), ("title", "authors"), 1_000
        )

    assert 2 == len(granted)
    # Planning: the predecessor consent is no longer the library's envelope.
    for consent in granted:
        assert not effects.repository.field_effect_consent_is_current(consent)
    with pytest.raises(FieldEffectError) as raised:
        effects.repository.current_field_effect_consent("primary", "books", "title")
    assert "consent" == raised.value.code
    # Activity: only an envelope-current consent is projected, so the owner is
    # re-offered the library rather than told it is still enabled.
    activity = effects.repository.field_effect_activity_data(1, include_events=False)
    assert [] == activity["consented_destinations"]
    assert [] == activity["consented_libraries"]

    with pytest.raises(FieldEffectError, match="consent"):
        effects.repository.current_field_effect_consent("primary", "books", added)
    regranted = effects.repository.grant_field_effect_consents(
        "primary", ("books",), (added, "title"), 1_001
    )

    assert all(
        effects.repository.field_effect_consent_is_current(consent)
        for consent in regranted
    )
    assert {added, "title"} == {
        str(row["destination"])
        for row in cast(
            "list[dict[str, object]]",
            effects.repository.field_effect_activity_data(1, include_events=False)[
                "consented_destinations"
            ],
        )
    }


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
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
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
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
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
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
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

        def read_review_library(
            self, search, limit, offset, attention, library_labels=(), validated=None
        ):
            library_entered.set()
            return repository.read_review_library(
                search, limit, offset, attention, library_labels, validated
            )

    blocking = BlockingRepository()
    effects.service._repository = blocking
    root = Path(effects.temporary.name)
    password = root / "serialized-owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        blocking,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
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


def test_activity_reader_uses_a_separate_connection_during_a_held_writer(
    effects: _EffectEnvironment,
) -> None:
    """The Activity reader is not a second transaction on preparation's owner."""
    _plan(effects, suffix="c")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    writer_held = threading.Event()
    release_writer = threading.Event()
    completed = threading.Event()
    result: dict[str, object] = {}

    def hold_writer() -> None:
        effects.repository._connection.execute("BEGIN IMMEDIATE")
        writer_held.set()
        assert release_writer.wait(2)
        effects.repository._connection.rollback()

    def read_activity() -> None:
        result.update(
            reader.field_effect_activity_page(
                search=None,
                event_type="all",
                occurred_after=0,
                page=1,
                page_size=25,
            )
        )
        completed.set()

    writer = threading.Thread(target=hold_writer)
    activity = threading.Thread(target=read_activity)
    try:
        writer.start()
        assert writer_held.wait(1)
        activity.start()
        assert completed.wait(1)
        assert reader._connection is not effects.repository._connection
        assert result["events"]
        assert not reader._connection.in_transaction
    finally:
        release_writer.set()
        writer.join(2)
        activity.join(2)
        reader.close()
    assert not writer.is_alive()
    assert not activity.is_alive()


def test_activity_projection_bounds_delete_journal_pending_writer_convoy(
    effects: _EffectEnvironment,
) -> None:
    """A pending DELETE-journal writer fails Activity in under one second."""

    plan = _plan(effects, suffix="e")
    reader_a = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    reader_c = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    reader_c.configure_activity_page_reader()
    writer_ready = threading.Event()
    writer_committed = threading.Event()
    writer_errors: list[BaseException] = []
    activity_result: dict[str, object] = {}

    def pending_writer() -> None:
        connection = sqlite3.connect(effects.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=8000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE field_effects SET updated_at=updated_at WHERE plan_id=?",
                (plan.plan.plan_id,),
            )
            writer_ready.set()
            connection.commit()
            writer_committed.set()
        except BaseException as error:
            writer_errors.append(error)
        finally:
            connection.close()

    writer = threading.Thread(target=pending_writer)
    try:
        assert reader_a._connection.execute("PRAGMA journal_mode").fetchone() == (
            "delete",
        )
        reader_a._connection.execute("BEGIN")
        reader_a._connection.execute("SELECT 1 FROM field_effects LIMIT 1").fetchone()
        writer.start()
        assert writer_ready.wait(1)
        # Readiness precedes COMMIT: prove the writer has reached PENDING,
        # where a new reader is refused, before timing the Activity read.
        with closing(
            sqlite3.connect(effects.database_path, timeout=0, isolation_level=None)
        ) as probe:
            deadline = time.monotonic() + 1
            while True:
                try:
                    with closing(
                        probe.execute("SELECT 1 FROM field_effects LIMIT 1")
                    ) as cursor:
                        cursor.fetchone()
                except sqlite3.OperationalError as error:
                    assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
                    break
                assert time.monotonic() < deadline
        started = time.monotonic()
        with pytest.raises(FieldEffectError, match="^persistence$") as raised:
            reader_c.field_effect_activity_page(
                search=None,
                event_type="all",
                occurred_after=0,
                page=1,
                page_size=25,
            )
        elapsed = time.monotonic() - started
        cause = raised.value.__cause__
        assert isinstance(cause, sqlite3.OperationalError)
        assert reader_c._field_effect_activity._activity_facts is not None
        activity_result.update(
            {
                "elapsed": elapsed,
                "sqlite_errorcode": cause.sqlite_errorcode,
                "sqlite_errorname": cause.sqlite_errorname,
            }
        )
    finally:
        if reader_a._connection.in_transaction:
            reader_a._connection.rollback()
        writer.join(9)
        reader_a.close()
        reader_c.close()
    assert writer_errors == []
    assert writer_committed.is_set()
    assert cast(float, activity_result["elapsed"]) < 1
    assert activity_result["sqlite_errorcode"] == sqlite3.SQLITE_BUSY
    assert activity_result["sqlite_errorname"] == "SQLITE_BUSY"


def _await_pending_writer(database_path: Path) -> None:
    """Wait until the writer's commit holds PENDING, so new reads are refused.

    ``writer_ready`` fires before ``commit()`` is called; until the commit
    takes PENDING a fresh read still succeeds, so a loaded runner could read
    on the first attempt and never exercise the busy retry.
    """

    deadline = time.monotonic() + 5
    while True:
        probe = sqlite3.connect(database_path, timeout=0, isolation_level=None)
        try:
            probe.execute("SELECT 1 FROM field_effects LIMIT 1").fetchone()
        except sqlite3.OperationalError as error:
            if "locked" in str(error):
                return
            raise
        finally:
            probe.close()
        assert time.monotonic() < deadline, "the writer never reached PENDING"
        time.sleep(0.005)


def _activity_read_after_short_delete_journal_convoy(
    effects: _EffectEnvironment,
    plan: FieldEffectOperation,
    *,
    method_name: str,
    call: Callable[[SQLiteMetadataReportRepository], object],
) -> object:
    reader_a = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    reader_c = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    reader_c.configure_activity_page_reader()
    writer_ready = threading.Event()
    writer_committed = threading.Event()
    first_attempt = threading.Event()
    writer_errors: list[BaseException] = []
    attempts = [0]
    projection = reader_c._field_effect_activity
    original = getattr(projection, method_name)

    def counted(*arguments, **keywords):
        attempts[0] += 1
        first_attempt.set()
        return original(*arguments, **keywords)

    def pending_writer() -> None:
        connection = sqlite3.connect(effects.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=8000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE field_effects SET updated_at=updated_at WHERE plan_id=?",
                (plan.plan.plan_id,),
            )
            writer_ready.set()
            connection.commit()
            writer_committed.set()
        except BaseException as error:
            writer_errors.append(error)
        finally:
            connection.close()

    def release_reader() -> None:
        # The convoy is held from the first attempt, not from when this thread
        # started: on a loaded runner the read can begin later than any fixed
        # delay, find the lock already gone, and never need the retry.
        first_attempt.wait(5)
        time.sleep(0.2)
        reader_a._connection.rollback()

    writer = threading.Thread(target=pending_writer)
    release = threading.Thread(target=release_reader)
    with patch.object(projection, method_name, counted):
        try:
            assert reader_a._connection.execute("PRAGMA journal_mode").fetchone() == (
                "delete",
            )
            reader_a._connection.execute("BEGIN")
            reader_a._connection.execute(
                "SELECT 1 FROM field_effects LIMIT 1"
            ).fetchone()
            writer.start()
            assert writer_ready.wait(1)
            _await_pending_writer(effects.database_path)
            release.start()
            started = time.monotonic()
            result = call(reader_c)
            elapsed = time.monotonic() - started
        finally:
            if reader_a._connection.in_transaction:
                reader_a._connection.rollback()
            release.join(2)
            writer.join(9)
            reader_a.close()
            reader_c.close()
    assert writer_errors == []
    assert writer_committed.is_set()
    assert attempts[0] >= 2
    assert elapsed < 1
    return result


def test_activity_projection_retries_a_short_delete_journal_convoy(
    effects: _EffectEnvironment,
) -> None:
    """A bounded busy retry returns the current Activity page after writer commit."""

    result = cast(
        "dict[str, object]",
        _activity_read_after_short_delete_journal_convoy(
            effects,
            _plan(effects, suffix="f"),
            method_name="_field_effect_activity_page_once",
            call=lambda reader: reader.field_effect_activity_page(
                search=None,
                event_type="all",
                occurred_after=0,
                page=1,
                page_size=25,
            ),
        ),
    )
    assert result["events"]


def test_activity_backup_readiness_retries_a_short_delete_journal_convoy(
    effects: _EffectEnvironment,
) -> None:
    """Contention is retried before Activity reports backup readiness false."""

    assert (
        _activity_read_after_short_delete_journal_convoy(
            effects,
            _plan(effects, suffix="0"),
            method_name="_field_effect_backup_ready_once",
            call=lambda reader: reader.field_effect_backup_ready(),
        )
        is True
    )


def test_activity_transitions_retry_a_short_delete_journal_convoy(
    effects: _EffectEnvironment,
) -> None:
    """Event expansion applies the same bounded retry as the Activity page."""

    plan = _plan(effects, suffix="b")
    event_id = effects.repository._connection.execute(
        "SELECT event_id FROM field_effect_events WHERE plan_id=? AND sequence=1",
        (plan.plan.plan_id,),
    ).fetchone()[0]
    transitions = cast(
        "tuple[dict[str, object], ...]",
        _activity_read_after_short_delete_journal_convoy(
            effects,
            plan,
            method_name="_field_effect_activity_transitions_once",
            call=lambda reader: reader.field_effect_activity_transitions(event_id),
        ),
    )
    assert [event["state"] for event in transitions] == ["planned"]


@pytest.mark.parametrize("configured", [False, True])
def test_activity_projection_stamps_inside_snapshot_and_validates_a_cache_hit_once(
    effects: _EffectEnvironment,
    configured: bool,
) -> None:
    """A miss stamps its snapshot; a hit performs only one current check."""

    _plan(effects, suffix="d")
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    if configured:
        reader.configure_activity_page_reader()
    projection = reader._field_effect_activity
    original_revision = projection._activity_projection_revision
    original_page = projection._field_effect_activity_page_uncached
    revision_transactions: list[bool] = []
    page_calls = [0]

    def observed_revision() -> tuple[int, int]:
        revision_transactions.append(projection._connection.in_transaction)
        return original_revision()

    def counted_page(
        **query: object,
    ) -> tuple[
        dict[str, object], tuple[int, int], int | None, tuple[int, int, int, int]
    ]:
        page_calls[0] += 1
        return original_page(**query)

    with (
        patch.object(projection, "_activity_projection_revision", observed_revision),
        patch.object(projection, "_field_effect_activity_page_uncached", counted_page),
    ):
        try:
            result = reader.field_effect_activity_page(
                search=None,
                event_type="all",
                occurred_after=0,
                page=1,
                page_size=25,
            )
            if configured:
                from mechanical_librarian.domain.activity_records import (
                    ReadableActivityEvent,
                )

                assert result["report_documents"] == {}
                assert all(
                    type(event) is ReadableActivityEvent for event in result["events"]
                )
            else:
                assert {
                    key: value.to_json()
                    for key, value in result.pop("validated_reports").items()
                } == result["report_documents"]
            assert (
                reader.field_effect_activity_page(
                    search=None,
                    event_type="all",
                    occurred_after=0,
                    page=1,
                    page_size=25,
                )
                == result
            )
        finally:
            reader.close()
    assert result["events"]
    assert page_calls == [1]
    assert revision_transactions == (
        [True, True, True] if configured else [True, False]
    )


def test_activity_projection_never_retries_a_nonbusy_error(
    effects: _EffectEnvironment,
) -> None:
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    reader.configure_activity_page_reader()
    assert reader._connection.execute("PRAGMA query_only").fetchone() == (1,)
    with pytest.raises(sqlite3.OperationalError) as refused:
        reader._connection.execute("DELETE FROM field_effect_events WHERE 0")
    assert refused.value.sqlite_errorcode == sqlite3.SQLITE_READONLY
    attempts = [0]

    def unavailable_page(**_query):
        attempts[0] += 1
        try:
            raise sqlite3.OperationalError()
        except sqlite3.OperationalError as error:
            raise FieldEffectError("persistence") from error

    with patch.object(
        reader._field_effect_activity,
        "_field_effect_activity_page_once",
        unavailable_page,
    ):
        try:
            with pytest.raises(FieldEffectError, match="^persistence$"):
                reader.field_effect_activity_page(
                    search=None,
                    event_type="all",
                    occurred_after=0,
                    page=1,
                    page_size=25,
                )
        finally:
            reader.close()
    assert attempts == [1]


@pytest.mark.parametrize(
    "field,category,option",
    (
        ("title", "title_layout", "preserve"),
        ("title", "title_layout", "title_and_subtitle"),
        ("authors", "contributor_names", "preserve"),
        ("authors", "contributor_names", "canonical"),
    ),
)
def test_audiobook_title_author_preservation_is_one_durable_no_change(
    effects: _EffectEnvironment, field: str, category: str, option: str
) -> None:
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_audiobook_identity,
    )
    from mechanical_librarian.domain.metadata_report import (
        TargetDestination,
        TargetField,
        TargetFieldState,
    )

    report = synthetic_audiobook_bibliographic_report()
    assert admitted_audiobook_identity(report)
    fields = tuple(
        TargetField(item.destination, TargetFieldState.VALUE, "Synthetic Journey")
        if item.destination is TargetDestination.TITLE
        else item
        for item in report.target.fields
    )
    report = MetadataReport.create(
        target=replace(report.target, fields=fields),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        audio=report.audio,
    )
    identity = f"{category}:{option}:audiobook_{field}_preserve"
    with patch.object(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS | {identity},
    ):
        case_id, _ = publish_audiobook_preview(
            effects.repository, report, (category,), selected_options={category: option}
        )
        effects.repository.persist_shadow_outcome(case_id, 4)
        documents = effects.repository.get_review_case_decisions(case_id)
        assert len(documents) == 1
        decision = AutomaticDecision.from_json(documents[0])
        assert decision.destination == field and decision.outcome == "keep_current"
        assert decision.policy_option == f"{option}:audiobook_{field}_preserve"
        authority = PersistedFieldEffectAuthority(effects.repository)
        lane = ContinuousGuardedApplication(
            effects.repository,
            {("primary", "audio"): effects.service},
            {("primary", "audio"): effects.capabilities},
            authority,
            (("primary", "audio"),),
            effects.clock,
            lambda *_: None,
        )
        with patch.object(
            authority,
            "resolve",
            side_effect=AssertionError("no-change reached effect guards"),
        ):
            assert lane.schedule() == lane.schedule() == 0
        assert effects.target._values == {}
        assert effects.repository.pending_field_effect_decision_documents(100) == ()
        assert effects.repository.pending_field_effect_decision_counts() == ()
        assert effects.repository._connection.execute(
            "SELECT COUNT(*) FROM field_effects"
        ).fetchone() == (0,)
        events = effects.repository._connection.execute(
            "SELECT decision_id,destination,event_id FROM field_effect_no_changes"
        ).fetchall()
        assert len(events) == 1 and events[0][:2] == (decision.decision_id, field)
        effects.repository.close()
        effects.repository = open_repository(effects.database_path)
        assert effects.repository.get_review_case_decisions(case_id) == documents
        assert (
            effects.repository._connection.execute(
                "SELECT decision_id,destination,event_id FROM field_effect_no_changes"
            ).fetchall()
            == events
        )


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


@pytest.fixture
def audiobook_pair(tmp_path, effects):
    out = tmp_path
    report = synthetic_audiobook_bibliographic_report()
    case = FieldEffectTargetCase()
    env = effects
    state = SimpleNamespace(repo=None, case=case, env=env)
    authors = next(
        f.value for f in report.target.fields if f.destination.value == "authors"
    )
    assert type(authors) is tuple and all(type(name) is str for name in authors)
    try:
        case.detail.update(isInvalid=False, isMissing=False)
        case.detail["media"].update(
            audioFiles=[synthetic_audio_file("synthetic.m4b")],
            metadata={
                "title": "Synthetic Journey: A Tale of Courage",
                "authors": [{"name": name} for name in authors],
            },
        )
        reader = AudiobookshelfItemReader(
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=case.detail,
                    )
                )
            ),
            endpoint="https://abs.example.invalid",
            library_id="library-1",
            service_path="/library",
            maximum_bytes=65536,
        )
        before = reader.read("item-1")
        report = MetadataReport.create(
            target=replace(
                report.target, connection="primary", item="item-1", fields=before.fields
            ),
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            audio=report.audio,
        )
        state.report = report
        state.reader = reader
        state.repo = open_repository(out / "state.sqlite")
        state.case_id, _ = publish_audiobook_preview(
            state.repo,
            report,
            ("title_layout", "subtitle"),
            selected_options={
                "title_layout": "title_and_subtitle",
                "subtitle": "corroborated_split",
            },
        )
        state.repo.persist_shadow_outcome(state.case_id, 4)
        state.decisions = {
            d.destination: d
            for raw in state.repo.get_review_case_decisions(state.case_id)
            if (d := AutomaticDecision.from_json(raw)).destination
            in ("title", "subtitle")
        }
        state.repo.grant_field_effect_consents(
            "primary", ("audio",), ("title", "subtitle"), 5
        )

        def reopen():
            state.repo.close()
            state.repo = open_repository(out / "state.sqlite")
            state.authority = PersistedFieldEffectAuthority(state.repo)
            state.service = env.persisted_service(
                state.repo, case.target, state.authority
            )

        state.reopen = reopen
        reopen()
        state.plan = lambda: state.service.plan_title_subtitle(
            state.decisions["title"],
            state.decisions["subtitle"],
            title_capability_digest=CAPABILITY,
            subtitle_capability_digest=CAPABILITY,
        )
        state.patches = lambda: [
            json.loads(r.content) for r in case.requests if r.method == "PATCH"
        ]
        yield state
    finally:
        if state.repo:
            state.repo.close()
        case.close()


@pytest.mark.parametrize(
    "reason", ("field_unavailable", "insufficient_permission", "connection_unavailable")
)
def test_audiobook_pair_recovers_companion_after_schedule_cursor(
    audiobook_pair, reason
):
    pair = audiobook_pair
    lane = ContinuousGuardedApplication(
        pair.repo,
        {("primary", "audio"): pair.service},
        {("primary", "audio"): pair.env.capabilities},
        pair.authority,
        (("primary", "audio"),),
        pair.env.clock,
        lambda *_: None,
    )
    # The pair shares one `occurred_at`, so `ORDER BY occurred_at, decision_id`
    # breaks the tie on a content hash that includes the F46 qualification
    # digest: every re-pin re-rolls which member is visited first. The order is
    # incidental, so this reads it rather than pinning it. The pair plan forms
    # only at the title visit, so when title is visited first a transient
    # suspends `title` and same-page recovery needs the cursor restart that
    # `wake()` performs -- which production gets from the readiness poll.
    pending = pair.repo.pending_field_effect_decision_documents(100)
    visited = [AutomaticDecision.from_json(raw).destination for raw in pending]
    assert sorted(visited) == ["subtitle", "title"]
    first = visited[0]
    pair.env.capabilities.category = reason
    assert lane.schedule(limit=1) == 0
    assert pair.repo.field_effect_capability_suspension("primary", first) == reason
    pair.env.capabilities.category = "allowed"
    if first == "title":
        lane.wake()
    assert lane.schedule(limit=1) == 1
    assert pair.repo.field_effect_capability_suspension("primary", first) is None
    assert lane.schedule() == 0
    row = pair.repo._connection.execute("SELECT plan_id FROM field_effects").fetchone()
    assert row is not None
    result = pair.service.apply(row[0])
    assert result.state == "verified"
    assert pair.service.apply(row[0]).state == "verified"
    assert len(pair.patches()) == 1
    assert pair.patches()[0]["metadata"] == {
        "title": "Synthetic Journey",
        "subtitle": "A Tale of Courage",
    }
    pair.reopen()
    assert pair.repo.get_field_effect(row[0]).to_data() == result.to_data()
    events = [json.loads(raw) for raw in pair.repo.field_effect_event_documents(row[0])]
    assert len(events) == 4
    assert events[-1]["desired_value"] == "Synthetic Journey"
    assert events[-1]["subtitle_companion"]["desired_value"] == "A Tale of Courage"
    assert pair.repo.pending_field_effect_decision_documents(100) == ()


@pytest.mark.parametrize(
    "boundary", ("unexpected_effect_behavior", "denied", "paused", "owner", "consent")
)
def test_audiobook_pair_recovery_preserves_remaining_guards(audiobook_pair, boundary):
    pair = audiobook_pair
    lane = ContinuousGuardedApplication(
        pair.repo,
        {("primary", "audio"): pair.service},
        {("primary", "audio"): pair.env.capabilities},
        pair.authority,
        (("primary", "audio"),),
        pair.env.clock,
        lambda *_: None,
    )
    # The visited member is a hash tie-break (see the schedule-cursor test), and
    # recovering from a transient on the title visit needs the cursor restart
    # production gets from its readiness poll.
    visited = [
        AutomaticDecision.from_json(raw).destination
        for raw in pair.repo.pending_field_effect_decision_documents(100)
    ]
    pair.env.capabilities.category = "field_unavailable"
    assert lane.schedule(limit=1) == 0
    pair.env.capabilities.category = "allowed"
    if visited[0] == "title":
        lane.wake()
    if boundary == "unexpected_effect_behavior":
        pair.repo.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="subtitle",
            reason=boundary,
            changed_at=pair.env.clock(),
        )
    elif boundary == "denied":
        pair.env.capabilities.category = "field_unavailable"
    elif boundary == "paused":
        pair.repo.set_field_effect_paused(True, pair.env.clock())
    elif boundary == "owner":
        _paired_owner_keep(pair, "subtitle")
    else:
        consent = pair.repo.current_field_effect_consent("primary", "audio", "subtitle")
        pair.repo.revoke_field_effect_consent(consent.consent_id, pair.env.clock())
    if boundary == "owner":
        with pytest.raises(FieldEffectError, match="authority_binding"):
            lane.schedule()
    elif boundary == "paused":
        assert lane.schedule() == 1
        row = pair.repo._connection.execute(
            "SELECT plan_id FROM field_effects"
        ).fetchone()
        assert row is not None
        with pytest.raises(GuardedApplicationError, match="paused"):
            pair.service.apply(row[0])
    else:
        assert lane.schedule() == 0
    if boundary == "unexpected_effect_behavior":
        assert (
            pair.repo.field_effect_capability_suspension("primary", "subtitle")
            == boundary
        )
    assert pair.patches() == []
    assert pair.repo._connection.execute(
        "SELECT count(*) FROM field_effects WHERE state='verified'"
    ).fetchone() == (0,)


def test_audiobook_pair_happy_pair_scheduler_reopen_and_activity(audiobook_pair):
    lane = ContinuousGuardedApplication(
        audiobook_pair.repo,
        {("primary", "audio"): audiobook_pair.service},
        {("primary", "audio"): audiobook_pair.env.capabilities},
        audiobook_pair.authority,
        (("primary", "audio"),),
        audiobook_pair.env.clock,
        lambda *_: None,
    )
    assert len(audiobook_pair.repo.pending_field_effect_decision_documents(100)) == 2
    assert lane.schedule() == 1
    assert lane.schedule() == 0
    assert audiobook_pair.repo.pending_field_effect_decision_documents(100) == ()
    assert audiobook_pair.repo.pending_field_effect_decision_counts() == ()
    row = audiobook_pair.repo._connection.execute(
        "SELECT plan_id FROM field_effects"
    ).fetchone()
    assert row
    first = audiobook_pair.service.apply(row[0])
    assert first.state == "verified"
    assert audiobook_pair.service.apply(row[0]).state == "verified"
    assert len(audiobook_pair.patches()) == 1
    audiobook_pair.reopen()
    assert audiobook_pair.repo.get_field_effect(row[0]).state == "verified"
    events = audiobook_pair.repo.field_effect_event_documents(row[0])
    assert len(events) == 4
    for raw in events:
        assert (
            json.loads(raw)["subtitle_companion"]["desired_value"]
            == "A Tale of Courage"
        )
    activity = audiobook_pair.repo.field_effect_activity_data(50)
    assert activity
    page = audiobook_pair.repo.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=50
    )
    verified = next(event for event in page["events"] if event["state"] == "verified")
    assert verified["record"]["desired_value"] == "Synthetic Journey"
    assert (
        verified["record"]["subtitle_companion"]["desired_value"] == "A Tale of Courage"
    )

    # Actual owner-readable composer uses the two-member durable event.
    event = {
        "destination": "title",
        "origin": "automatic_policy",
        "state": "verified",
        "event_type": "field_effect",
        "record": {
            "source_kind": "field_effect",
            **{
                k: v
                for k, v in json.loads(events[-1]).items()
                if k in ("pre_value", "desired_value", "subtitle_companion")
            },
        },
    }
    readable_activity_event(event, audiobook_pair.report)
    assert event["field_label"] == "Title and subtitle"
    assert event["change"] == "Split title and subtitle"
    assert any(
        x["value"] == "Both title and subtitle confirmed" for x in event["evidence"]
    )


@pytest.mark.parametrize("field", ["title", "subtitle"])
def test_audiobook_pair_scalar_path_refuses_pair(audiobook_pair, field):
    d = audiobook_pair.decisions[field]
    s = audiobook_pair.authority.resolve(d)
    with pytest.raises(GuardedApplicationError, match="paired_authority_required"):
        audiobook_pair.service.plan(
            d,
            connection=s.connection,
            library=s.library,
            work_id=s.work_id,
            destination=field,
            pre_value=s.pre_value,
            desired_value=s.decision_value,
            target_fingerprint=s.target_fingerprint,
            target_version=s.target_version,
            dependency_digests=s.dependency_digests,
            capability_digest=CAPABILITY,
        )
    assert audiobook_pair.repo._connection.execute(
        "SELECT COUNT(*) FROM field_effects"
    ).fetchone() == (0,)


@pytest.mark.parametrize("field", ["title", "subtitle"])
@pytest.mark.parametrize("boundary", ["before", "claim"])
def test_audiobook_pair_either_consent_revocation_zero_patch(
    audiobook_pair, field, boundary
):
    op = audiobook_pair.plan()
    consent = (
        op.plan.consent if field == "title" else op.plan.subtitle_companion.consent
    )

    def revoke(_=None):
        audiobook_pair.repo.revoke_field_effect_consent(
            consent.consent_id, audiobook_pair.env.clock()
        )

    if boundary == "before":
        revoke()
    else:
        audiobook_pair.env.claim_hook.callback = revoke
    try:
        audiobook_pair.service.apply(op.plan.plan_id)
    except GuardedApplicationError as e:
        assert e.code in ("claim_conflict", "consent")
    assert not audiobook_pair.patches()
    assert audiobook_pair.repo.get_field_effect(op.plan.plan_id).state in (
        "superseded",
        "stale",
    )


def _paired_owner_keep(audiobook_pair, field):
    case = ReviewCase.create_from_data(
        json.loads(audiobook_pair.repo.get_review_case(audiobook_pair.case_id))["case"]
    )
    proposal = next(x for x in case.proposals if x["destination"] == field)
    decision = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=field,
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="paired-owner-keep-" + field,
        occurred_at=audiobook_pair.env.clock(),
    )
    audiobook_pair.repo.record_human_decision(decision)


@pytest.mark.parametrize("field", ["title", "subtitle"])
@pytest.mark.parametrize("boundary", ["before", "claim"])
def test_audiobook_pair_either_owner_final_zero_patch(audiobook_pair, field, boundary):
    op = audiobook_pair.plan()
    if boundary == "before":
        _paired_owner_keep(audiobook_pair, field)
    else:
        audiobook_pair.env.claim_hook.callback = lambda _: _paired_owner_keep(
            audiobook_pair, field
        )
    try:
        audiobook_pair.service.apply(op.plan.plan_id)
    except GuardedApplicationError as e:
        assert e.code in ("claim_conflict", "authority")
    assert not audiobook_pair.patches()
    assert audiobook_pair.repo.get_field_effect(op.plan.plan_id).state in (
        "superseded",
        "stale",
    )


@pytest.mark.parametrize("field", ["title", "subtitle"])
def test_audiobook_pair_either_target_drift_zero_patch(audiobook_pair, field):
    op = audiobook_pair.plan()
    audiobook_pair.case.detail["media"]["metadata"][field] = "External edit"
    result = audiobook_pair.service.apply(op.plan.plan_id)
    assert result.state == "stale" and result.failure == "target_changed"
    assert not audiobook_pair.patches()


@pytest.mark.parametrize("field", ["title", "subtitle"])
def test_audiobook_pair_either_capability_zero_patch(audiobook_pair, field):
    op = audiobook_pair.plan()
    audiobook_pair.repo.suspend_field_effect_capability(
        scope="destination",
        connection="primary",
        destination=field,
        reason="field_unavailable",
        changed_at=audiobook_pair.env.clock(),
    )
    result = audiobook_pair.service.apply(op.plan.plan_id)
    assert result.state == "stale" and result.failure == "capability"
    assert not audiobook_pair.patches()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("partial", "stale"),
        ("partial_timeout", "stale"),
        ("timeout", "verified"),
        ("read_failure", "verified"),
        ("crash", "verified"),
    ],
)
def test_audiobook_pair_partial_timeout_read_failure_and_restart(
    audiobook_pair, mode, expected
):
    op = audiobook_pair.plan()
    audiobook_pair.case.paired_mode = mode
    if mode == "crash":
        with pytest.raises(KeyboardInterrupt):
            audiobook_pair.service.apply(op.plan.plan_id)
        assert audiobook_pair.repo.get_field_effect(op.plan.plan_id).state == "applying"
    else:
        audiobook_pair.service.apply(op.plan.plan_id)
    audiobook_pair.reopen()
    result = audiobook_pair.service.apply(op.plan.plan_id)
    assert result.state == expected
    if expected == "stale":
        assert result.failure == "third_value"
    assert len(audiobook_pair.patches()) == 1
    assert audiobook_pair.service.apply(op.plan.plan_id).state == expected
    assert len(audiobook_pair.patches()) == 1
    values = {
        x.destination.value: x.value
        for x in audiobook_pair.reader.read("item-1").fields
    }
    assert values["title"] == "Synthetic Journey"
    assert (values["subtitle"] is None) == (expected == "stale")


@pytest.mark.parametrize(
    "key,value",
    [
        ("desired_value", "Different subtitle"),
        ("pre_value", "Injected"),
        ("target_version", "wrong-version"),
        ("capability_digest", "a" * 64),
        ("dependency_digests", ["a" * 64]),
        ("bundle_id", "automatic_bundle_" + "a" * 64),
    ],
)
def test_audiobook_pair_companion_serialized_tamper_refuses(audiobook_pair, key, value):
    op = audiobook_pair.plan()
    raw = op.plan.to_data()
    raw["subtitle_companion"][key] = value
    with pytest.raises(FieldEffectError):
        FieldEffectPlan.from_data(raw)
    assert not audiobook_pair.patches()


def test_audiobook_pair_competing_claim_only_one_wins(audiobook_pair):
    op = audiobook_pair.plan()
    one = audiobook_pair.repo.claim_field_effect(
        op.plan.plan_id, "planned", "a" * 64, audiobook_pair.env.clock()
    )
    assert one.state == "claimed"
    with pytest.raises(FieldEffectError, match="claim_conflict"):
        audiobook_pair.repo.claim_field_effect(
            op.plan.plan_id, "planned", "b" * 64, audiobook_pair.env.clock()
        )
    assert audiobook_pair.service.apply(op.plan.plan_id).state == "verified"
    assert len(audiobook_pair.patches()) == 1


@pytest.mark.parametrize(
    "state,failure,confirmed",
    [
        ("verified", "", True),
        ("planned", "", False),
        ("reconciling", "readback", False),
        ("stale", "third_value", False),
        ("retryable", "target_failure", False),
    ],
)
def test_audiobook_pair_paired_activity_words(
    audiobook_pair, state, failure, confirmed
):
    op = audiobook_pair.plan()
    record = {
        "source_kind": "field_effect",
        "pre_value": op.plan.pre_value,
        "desired_value": op.plan.desired_value,
        "subtitle_companion": op.plan.subtitle_companion.to_data(),
        "failure": failure,
    }
    event = {
        "destination": "title",
        "origin": "automatic_policy",
        "state": state,
        "event_type": "field_effect",
        "record": record,
    }
    readable_activity_event(event, audiobook_pair.report)
    assert event["field_label"] == "Title and subtitle"
    if state == "reconciling":
        assert event["change"] == "Paired title and subtitle change unconfirmed"
    assert (event["change"] == "Split title and subtitle") == confirmed
    assert ("Both title and subtitle confirmed" in str(event["evidence"])) == confirmed
    assert "No write was applied" not in str(event["evidence"])


@pytest.mark.parametrize("field", ["title", "subtitle"])
@pytest.mark.parametrize("state", ["claimed", "applying"])
def test_audiobook_pair_owner_command_after_claim_refuses_equally(
    audiobook_pair, field, state
):
    from mechanical_librarian.domain.human_decision import HumanDecisionError

    op = audiobook_pair.plan()
    audiobook_pair.repo.claim_field_effect(
        op.plan.plan_id, "planned", "c" * 64, audiobook_pair.env.clock()
    )
    if state == "applying":
        audiobook_pair.repo.transition_field_effect(
            op.plan.plan_id, "claimed", "applying", audiobook_pair.env.clock()
        )
    before = audiobook_pair.repo.get_field_effect(op.plan.plan_id).to_data()
    with pytest.raises(HumanDecisionError, match="already_decided"):
        _paired_owner_keep(audiobook_pair, field)
    assert audiobook_pair.repo.get_field_effect(op.plan.plan_id).to_data() == before
    assert not audiobook_pair.patches()
    assert (
        len(audiobook_pair.repo.get_review_case_decisions(audiobook_pair.case_id)) == 2
    )


def test_audiobook_pair_joint_qualification_withdrawal_after_claim_stops_pair(
    audiobook_pair,
):
    op = audiobook_pair.plan()
    audiobook_pair.repo.claim_field_effect(
        op.plan.plan_id, "planned", "c" * 64, audiobook_pair.env.clock()
    )
    with patch.object(
        audiobook_decision_qualification, "F46_PASSING_PAIRED_OPTIONS", frozenset()
    ):
        result = audiobook_pair.service.apply(op.plan.plan_id)
    assert result.state == "stale" and result.failure == "authority"
    assert not audiobook_pair.patches()


@pytest.mark.parametrize("field", ["title", "subtitle"])
def test_audiobook_pair_owner_final_before_plan_refuses(audiobook_pair, field):
    _paired_owner_keep(audiobook_pair, field)
    with pytest.raises((FieldEffectError, GuardedApplicationError)):
        audiobook_pair.plan()
    assert audiobook_pair.repo._connection.execute(
        "SELECT COUNT(*) FROM field_effects"
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "member_key",
    ["desired_value", "pre_value", "target_version", "dependency_digests", "bundle_id"],
)
def test_audiobook_pair_recomputed_plan_tamper_still_refuses_persist(
    audiobook_pair, member_key
):
    op = audiobook_pair.plan()
    comp = op.plan.subtitle_companion
    changed = replace(
        comp,
        **{
            member_key: {
                "desired_value": "Wrong subtitle",
                "pre_value": "Invented original",
                "target_version": "wrong-version",
                "dependency_digests": ("a" * 64,),
                "bundle_id": "automatic_bundle_" + "a" * 64,
            }[member_key]
        },
    )
    values = {
        k: getattr(op.plan, k)
        for k in (
            "connection",
            "library",
            "work_id",
            "destination",
            "pre_value",
            "desired_value",
            "target_fingerprint",
            "target_version",
            "dependency_digests",
            "authority",
            "consent",
            "capability_digest",
            "created_at",
        )
    }
    altered = FieldEffectPlan.create(**values, subtitle_companion=changed)
    from mechanical_librarian.domain.field_effect import FieldEffectOperation

    with pytest.raises(FieldEffectError, match="authority_binding"):
        audiobook_pair.repo.record_field_effect(
            FieldEffectOperation(altered, updated_at=altered.created_at)
        )
    assert audiobook_pair.repo._connection.execute(
        "SELECT COUNT(*) FROM field_effects"
    ).fetchone() == (1,)
    assert not audiobook_pair.patches()


@pytest.mark.parametrize("supplied", (True, False, None))
@pytest.mark.parametrize("current", (True, False, None))
def test_audiobook_adult_product_only_sets_true_and_otherwise_finishes_without_write(
    effects: _EffectEnvironment, supplied: bool | None, current: bool | None
) -> None:
    from mechanical_librarian.domain.metadata_report import (
        TargetDestination,
        TargetField,
        TargetFieldState,
    )

    report = synthetic_audiobook_bibliographic_report(adult_product=supplied)
    fields = tuple(
        TargetField(
            field.destination,
            TargetFieldState.MISSING if current is None else TargetFieldState.VALUE,
            current,
        )
        if field.destination is TargetDestination.EXPLICIT
        else field
        for field in report.target.fields
    )
    report = MetadataReport.create(
        target=replace(report.target, fields=fields),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        audio=report.audio,
    )
    identities = frozenset(
        audiobook_decision_qualification.F46_IDENTITIES_BY_DESTINATION["explicit"]
    )
    with patch.object(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS | identities,
    ):
        case_id, _ = publish_audiobook_preview(
            effects.repository,
            report,
            ("explicit",),
            selected_options={"explicit": "adult_product"},
        )
        effects.repository.persist_shadow_outcome(case_id, 4)
        decisions = effects.repository.get_review_case_decisions(case_id)
        assert len(decisions) == 1
        decision = AutomaticDecision.from_json(decisions[0])
        changes = supplied is True and current is not True
        assert decision.destination == "explicit"
        assert decision.outcome == ("candidate" if changes else "keep_current")
        authority = PersistedFieldEffectAuthority(effects.repository)
        service = effects.persisted_service(
            effects.repository, effects.target, authority
        )
        if changes:
            snapshot = authority.resolve(decision)
            assert snapshot.pre_value is current
            assert snapshot.decision_value == {"kind": "boolean", "value": True}
            effects.repository.grant_field_effect_consents(
                "primary", ("audio",), ("explicit",), 5
            )
            operation = service.plan(
                decision,
                connection=snapshot.connection,
                library=snapshot.library,
                work_id=snapshot.work_id,
                destination="explicit",
                pre_value=current,
                desired_value=snapshot.decision_value,
                target_fingerprint=snapshot.target_fingerprint,
                target_version=snapshot.target_version,
                dependency_digests=snapshot.dependency_digests,
                capability_digest=CAPABILITY,
            )
            assert operation is not None
            effects.target.seed(operation.plan)
            assert service.apply(operation.plan.plan_id).state == "verified"
            assert service.apply(operation.plan.plan_id).state == "verified"
            assert (
                effects.target._values[("primary", "audio", "item-1", "explicit")]
                is True
            )
        else:
            lane = ContinuousGuardedApplication(
                effects.repository,
                {("primary", "audio"): service},
                {("primary", "audio"): effects.capabilities},
                authority,
                (("primary", "audio"),),
                effects.clock,
                lambda *_: None,
            )
            with patch.object(
                authority,
                "resolve",
                side_effect=AssertionError("no-change must precede guards"),
            ):
                assert lane.schedule() == lane.schedule() == 0
            assert effects.target._values == {}
            assert effects.repository.pending_field_effect_decision_documents(100) == ()
        counts = tuple(
            effects.repository._connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in ("field_effects", "field_effect_no_changes")
        )
        assert counts == ((1, 0) if changes else (0, 1))
        effects.repository.close()
        effects.repository = open_repository(effects.database_path)
        assert effects.repository.get_review_case_decisions(case_id) == decisions
        assert (
            tuple(
                effects.repository._connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in ("field_effects", "field_effect_no_changes")
            )
            == counts
        )


@pytest.mark.parametrize("ready", [True, False])
def test_pair_checks_one_shared_backup_before_member_guards(audiobook_pair, ready):
    op = audiobook_pair.plan()
    audiobook_pair.env.backup.ready_value = ready
    with (
        patch.object(
            audiobook_pair.service, "_preclaim", wraps=audiobook_pair.service._preclaim
        ) as preclaims,
        patch.object(
            audiobook_pair.env.backup, "ready", wraps=audiobook_pair.env.backup.ready
        ) as backup,
    ):
        result = audiobook_pair.service.apply(op.plan.plan_id)
        assert backup.call_count == preclaims.call_count == (2 if ready else 1)
        # Existing lifecycle checks once before recovery capture and once after it.
        # Each preclaim checks the shared backup once, not once per member.
        assert backup.call_args.args == (op.plan,)
    assert result.state == ("verified" if ready else "stale")
    assert len(audiobook_pair.patches()) == int(ready)
    assert audiobook_pair.service._clock is audiobook_pair.env.clock


@pytest.mark.parametrize(
    "changed", ["candidate", "citation", "combination", "report", "missing_companion"]
)
def test_actual_pair_reconstruction_refuses_changed_sources(audiobook_pair, changed):
    from mechanical_librarian.adapters.persistence.field_effect_validation import (
        _automatic_decision_value,
    )
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

    case = ReviewCase.create_from_data(
        json.loads(audiobook_pair.repo.get_review_case(audiobook_pair.case_id))["case"]
    )
    proposal = ProposalSet.from_json(case.proposal_document)
    raw = audiobook_pair.repo._connection.execute(
        "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=?",
        (audiobook_pair.case_id,),
    ).fetchone()[0]
    outcome = ShadowOutcomeSet.from_json(raw)
    for source in audiobook_pair.decisions.values():
        assert _automatic_decision_value(
            source, case, audiobook_pair.report, proposal, outcome
        )
    with pytest.raises(ValueError):
        report = audiobook_pair.report
        members = list(outcome.outcomes)
        index = next(i for i, x in enumerate(members) if x.destination == "subtitle")
        member = members[index]
        if changed == "candidate":
            members[index] = replace(
                member, candidate_id=audiobook_pair.decisions["title"].candidate_id
            )
        elif changed == "citation":
            members[index] = replace(member, citation_ids=("wrong-source-citation",))
        elif changed == "combination":
            members[index] = replace(member, combination_ids=("wrong-combination",))
        elif changed == "missing_companion":
            members.pop(index)
        else:
            report = MetadataReport.create(
                target=replace(
                    report.target,
                    fields=tuple(
                        replace(field, value="Changed source report")
                        if field.destination.value == "title"
                        else field
                        for field in report.target.fields
                    ),
                ),
                file=report.file,
                local_claims=report.local_claims,
                local_reasons=report.local_reasons,
                providers=report.providers,
                audio=report.audio,
            )
        altered = replace(outcome, outcomes=tuple(members))
        _automatic_decision_value(
            audiobook_pair.decisions["title"], case, report, proposal, altered
        )
    assert not audiobook_pair.patches()


@pytest.mark.parametrize(
    "field,mode",
    [
        ("title_layout", "ask"),
        ("title_layout", "ignore"),
        ("subtitle", "ask"),
        ("subtitle", "ignore"),
        ("title", "owner"),
        ("subtitle", "owner"),
    ],
)
def test_pair_admission_boundaries(tmp_path, field, mode):
    from mechanical_librarian.domain.metadata_policy import DEFAULT_OPTIONS

    report = synthetic_audiobook_bibliographic_report()
    options = dict(DEFAULT_OPTIONS)
    options.update(title_layout="title_and_subtitle", subtitle="corroborated_split")
    modes = {k: "unsupported" if k == "explicit" else "ask" for k in OPTION_CATALOGUE}
    modes.update(title_layout="automatic", subtitle="automatic")
    if mode != "owner":
        modes[field] = mode
    profile = MetadataPolicyProfile.create("Paired policy negative", options, modes)
    resolved = resolve_policy(profile, report.target.library)
    identities = {
        x
        for x in audiobook_decision_qualification.F46_AUDIOBOOK_IDENTITIES
        if x.startswith(
            ("title_layout:title_and_subtitle:", "subtitle:corroborated_split:")
        )
    }
    with (
        patch.object(
            audiobook_decision_qualification,
            "F46_PASSING_AUTOMATIC_OPTIONS",
            audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS | identities,
        ),
        patch.object(
            audiobook_decision_qualification,
            "F46_PASSING_PAIRED_OPTIONS",
            frozenset(
                {audiobook_decision_qualification.F46_TITLE_SUBTITLE_PAIR_IDENTITY}
            ),
        ),
    ):
        repo = open_repository(tmp_path / "state.sqlite")
        try:
            repo.enter_shadow_preview(1)
            repo.create_metadata_policy_draft(profile.canonical_json)
            repo.activate_metadata_policy(
                profile.policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                (
                    (
                        report.target.library,
                        resolved.resolved_id,
                        resolved.canonical_json,
                    ),
                ),
                2,
                expected_active_generation=1,
            )
            repo.activate_qualified_automatic(3)
            proposal = evaluate_report(
                report, resolved.resolved_id, human_review_destinations(resolved)
            )
            repo.publish(report, lambda: True)
            repo.publish_proposal(proposal)
            case_id = json.loads(repo.admit_review_case(proposal))["review_case_id"]
            expected: tuple[str, ...] = ()
            if mode == "owner":
                case = ReviewCase.create_from_data(
                    json.loads(repo.get_review_case(case_id))["case"]
                )
                field_proposal = next(
                    x for x in case.proposals if x["destination"] == field
                )
                owner = HumanDecision.create(
                    case,
                    proposal_id=field_proposal["proposal_id"],
                    target_fingerprint=case.target_fingerprint,
                    destination=field,
                    actor="owner",
                    outcome="keep_current",
                    selected_value=None,
                    idempotency_key="paired-before-outcome-" + field,
                    occurred_at=4,
                )
                repo.record_human_decision(owner)
                expected = (owner.to_json(),)
            repo.persist_shadow_outcome(case_id, 5)
            assert repo.get_review_case_decisions(case_id) == expected
        finally:
            repo.close()


@pytest.mark.parametrize(
    "negative",
    (
        "none",
        "changed_dimensions",
        "changed_digest",
        "stale_report",
        "owner_final",
        "consent",
    ),
)
def test_guarded_audiobook_cover_materiality_boundaries(tmp_path, effects, negative):
    with audiobook_cover_environment(
        tmp_path / "cover", [(80, 120), (640, 960)]
    ) as state:
        assert len(state.decisions) == 1
        target_case = audiobookshelf_targets.FieldEffectTargetCase()
        target_case.cover_bytes = state.originals[0]
        try:
            for original, decoded in zip(state.originals, state.decoded, strict=True):
                target_case.artifacts.stage(decoded.artifact, original, decoded.preview)
            state.repo.grant_field_effect_consents("primary", ("audio",), ("cover",), 5)
            authority = PersistedFieldEffectAuthority(state.repo)
            snapshot = authority.resolve(state.decisions[0])
            guarded = effects.persisted_service(
                state.repo, target_case.target, authority
            )
            operation = guarded.plan(
                state.decisions[0],
                connection=snapshot.connection,
                library=snapshot.library,
                work_id=snapshot.work_id,
                destination="cover",
                pre_value=snapshot.pre_value,
                desired_value=snapshot.decision_value,
                target_fingerprint=snapshot.target_fingerprint,
                target_version=snapshot.target_version,
                dependency_digests=snapshot.dependency_digests,
                capability_digest=CAPABILITY,
            )
            assert operation is not None
            if negative == "changed_dimensions":
                target_case.cover_bytes = synthetic_image("PNG", (300, 450))
            elif negative == "changed_digest":
                target_case.cover_bytes = state.originals[1]
            elif negative == "stale_report":
                report = state.report
                fields = tuple(
                    TargetField(
                        field.destination,
                        TargetFieldState.VALUE,
                        "Changed source generation",
                    )
                    if field.destination is TargetDestination.TITLE
                    else field
                    for field in report.target.fields
                )
                changed = MetadataReport.create(
                    target=replace(report.target, fields=fields),
                    file=report.file,
                    local_claims=report.local_claims,
                    local_reasons=report.local_reasons,
                    providers=report.providers,
                    audio=report.audio,
                    cover_sources=report.cover_sources,
                )
                state.repo.publish(
                    changed, lambda: True, expected_current=report.report_id
                )
            elif negative == "owner_final":
                proposal = next(
                    value
                    for value in state.case.proposals
                    if value["destination"] == "cover"
                )
                decision = HumanDecision.create(
                    state.case,
                    proposal_id=proposal["proposal_id"],
                    target_fingerprint=state.case.target_fingerprint,
                    destination="cover",
                    actor="owner",
                    outcome="keep_current",
                    selected_value=None,
                    idempotency_key="cover-owner-keep",
                    occurred_at=effects.clock(),
                )
                state.repo.record_human_decision(
                    decision,
                    cover_consideration_id=state.consideration["consideration_id"],
                    cover_confirmation={
                        "expected_selection_id": state.repo._connection.execute(
                            "SELECT selection_id FROM current_cover_selection_authorities"
                        ).fetchone()[0],
                        "confirm_unverified_match": False,
                    },
                )
            elif negative == "consent":
                state.repo.revoke_field_effect_consent(
                    operation.plan.consent.consent_id, effects.clock()
                )
            with patch.object(
                audiobookshelf_targets, "SYNTHETIC_COVER_PNG", state.originals[1]
            ):
                try:
                    result = guarded.apply(operation.plan.plan_id)
                except GuardedApplicationError:
                    assert negative != "none"
                    result = state.repo.get_field_effect(operation.plan.plan_id)
                if negative == "none":
                    assert result.state == "verified"
                    assert guarded.apply(operation.plan.plan_id).state == "verified"
            posts = [
                request for request in target_case.requests if request.method == "POST"
            ]
            assert len(posts) == int(negative == "none")
            if negative == "none":
                assert target_case.cover_bytes == state.originals[1]
            else:
                assert result.state in ("stale", "superseded", "verified")
        finally:
            target_case.close()
    with closing(open_repository(tmp_path / "cover" / "state.sqlite")) as reopened:
        assert (
            reopened.get_field_effect(operation.plan.plan_id).to_data()
            == result.to_data()
        )


@pytest.mark.parametrize(
    "field,category,option,identity,current,expected",
    (
        (
            "title",
            "title_layout",
            "preserve",
            "audiobook_title_replace",
            "Project Hail Mary (Unabridged)",
            "Project Hail Mary",
        ),
        (
            "tags",
            "tags",
            "fill_empty",
            "audiobook_tags_fill",
            None,
            ["First", "Shared", "Second"],
        ),
        (
            "tags",
            "tags",
            "add",
            "audiobook_tags_add",
            ("Owner tag",),
            ["Owner tag", "First", "Shared", "Second"],
        ),
        (
            "narrators",
            "contributor_names",
            "canonical",
            "audiobook_narrators_fill",
            None,
            ["Example Narrator"],
        ),
        (
            "description",
            "description_fill",
            "safe_plain_text",
            "audiobook_description_fill",
            None,
            "A voyage reveals a hidden city.",
        ),
    ),
)
@pytest.mark.parametrize(
    "mutation", ("missing_reference", "changed_reference", "changed_candidate")
)
def test_audiobook_selected_sources_survive_reopen_and_refuse_resealed_authority(
    tmp_path,
    monkeypatch,
    field,
    category,
    option,
    identity,
    current,
    expected,
    mutation,
):
    from mechanical_librarian.adapters.persistence.field_effect_validation import (
        _automatic_decision_value,
    )
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
    from tests.support.audiobook_reports import (
        synthetic_audiobook_product_lookup_report,
    )

    products = tuple(
        {
            "runtimeLengthMin": 970,
            "asin": f"B00000000{index}",
            "title": "Synthetic Journey",
            "authors": [{"name": "Example Author"}],
            "narrators": [{"name": "Example Narrator"}],
            "summary": summary,
            "genres": [{"type": "tag", "name": tag} for tag in tags],
        }
        for index, summary, tags in (
            (1, "A voyage reveals a hidden city.", ["First", "Shared"]),
            (
                2,
                "An explorer crosses the sea and discovers an ancient city.",
                ["Shared", "Second"],
            ),
        )
    )
    if field == "title":
        from tests.support.audiobook_reports import asserted_asin_report

        report = asserted_asin_report(provider_duration="58200.000")
    else:
        report = synthetic_audiobook_product_lookup_report(
            products, recording_runtime="58200.000"
        )
    if current is not None:
        report = MetadataReport.create(
            target=replace(
                report.target,
                fields=tuple(
                    TargetField(member.destination, TargetFieldState.VALUE, current)
                    if member.destination.value == field
                    else member
                    for member in report.target.fields
                ),
            ),
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            audio=report.audio,
        )
    database = tmp_path / "selected-sources.sqlite"
    with closing(open_repository(database, cross_thread=True)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository, report, (category,), selected_options={category: option}
        )
        repository.persist_shadow_outcome(case_id, 4)
        decision_document = next(
            document
            for document in repository.get_review_case_decisions(case_id)
            if json.loads(document)["destination"] == field
        )
    with closing(open_repository(database, cross_thread=True)) as repository:
        decision = AutomaticDecision.from_json(decision_document)
        raw_decision = json.loads(decision_document)
        connection = repository._connection
        proposal = ProposalSet.from_json(
            connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (decision.proposal_set_id,),
            ).fetchone()[0]
        )
        case = ReviewCase.from_json(
            connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (case_id,),
            ).fetchone()[0]
        )
        outcome = ShadowOutcomeSet.from_json(
            connection.execute(
                "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? "
                "ORDER BY evaluated_at DESC LIMIT 1",
                (case_id,),
            ).fetchone()[0]
        )
        assert (
            _automatic_decision_value(decision, case, report, proposal, outcome)[
                "value"
            ]
            == expected
        )
        from mechanical_librarian.application.review_web import (
            BrowseReviewLibrary,
            InspectCurrentReviewCase,
            ReviewWebError,
        )

        inspector = InspectCurrentReviewCase(repository)
        detail = inspector.run(case_id)
        projected = next(
            item for item in detail["fields"] if item["destination"] == field
        )
        assert projected["automatic_decision"]["selected_value"]["value"] == expected
        assert projected["human_decision"] is None
        assert not projected["needs_attention"]
        assert projected["decision_actions"] == ["keep_current", "accept"]
        raw_proposal = next(
            item for item in case.proposals if item["destination"] == field
        )
        assert projected["proposed_values"] == [
            item["value"]
            for item in raw_proposal["result"]["alternatives"]
            if item["assessment"] != "preserves_target"
        ]
        if field == "tags":
            assert len(projected["proposed_values"]) == 2
            assert (
                projected["automatic_decision"]["selected_value"]
                not in projected["proposed_values"]
            )
            if current is not None:
                assert projected["current"]["value"] == list(current)
                assert expected[: len(current)] == list(current)
        history = inspector.history(case_id)
        event = next(
            item
            for item in history["items"]
            if item.get("decision_id") == decision.decision_id
        )
        assert event["selected_value"]["value"] == expected
        library = BrowseReviewLibrary(repository).library(
            lens="all", search=None, limit=25, cursor=None
        )
        assert any(item["review_case_id"] == case_id for item in library["items"])
        password = tmp_path / "owner.hash"
        password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(password, 0o600)
        app = create_transport_test_app(
            repository,
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
                canonical_origin="https://review.test",
                owner_password_hash_file=str(password),
                session_lifetime_seconds=600,
            ),
            clock=lambda: 10,
        )
        with TestClient(app, base_url="https://review.test") as client:
            assert (
                client.post(
                    "/api/v1/session/login",
                    json={"password": "correct horse"},
                    headers={"Origin": "https://review.test"},
                ).status_code
                == 200
            )
            response = client.get(f"/api/v1/review-cases/{case_id}")
            assert response.status_code == 200
            api_field = next(
                item
                for item in response.json()["fields"]
                if item["destination"] == field
            )
            assert api_field["automatic_decision"] == projected["automatic_decision"]
            assert api_field["proposed_values"] == projected["proposed_values"]
            sample = client.get("/api/v1/metadata-policy/shadow/outcomes?limit=20")
            assert sample.status_code == 200
            assert sample.json() == app.state.review_application.policy.shadow_sample(
                20
            )
            assert next(
                item
                for item in sample.json()["items"]
                if item["outcome_set_id"] == outcome.outcome_set_id
            ) == {
                **json.loads(outcome.to_json()),
                "evaluated_at": dict(repository.list_shadow_outcomes(20))[
                    outcome.to_json()
                ],
            }
            assert sample.json()["authority"] == "none"
        original = json.loads(outcome.to_json())
        selected = next(
            member
            for member in original["outcomes"]
            if member["outcome_id"] == decision.source_outcome_id
        )
        candidates = tuple(
            candidate
            for provider in report.providers
            for candidate in (
                *provider.candidates,
                *provider.recording_lookup_candidates,
            )
        )
        kind = {
            "title": ClaimKind.TITLE,
            "tags": ClaimKind.TAG,
            "narrators": ClaimKind.CONTRIBUTOR,
            "description": ClaimKind.DESCRIPTION,
        }[field]
        expected_refs = {
            claim.reference
            for candidate in candidates
            for claim in candidate.claims
            if claim.kind is kind
            and claim.state is FieldState.VALID
            and (field != "narrators" or claim.role == "narrator")
            and (field != "description" or claim.value == expected)
        }
        if field == "title":
            expected_refs.update(
                claim.reference
                for claim in report.local_claims
                if claim.kind is ClaimKind.TITLE
            )
        assert set(selected["citation_ids"]) == expected_refs
        assert expected_refs <= set(projected["provenance"])
        evidence = inspector.evidence(case_id)
        assert evidence["missing_lineage_count"] == 0
        if field == "tags":
            observations = " ".join(item["observation"] for item in evidence["items"])
            assert all(tag in observations for tag in ("First", "Shared", "Second"))
        if field == "narrators":
            assert all(
                any(claim.reference in expected_refs for claim in candidate.claims)
                for candidate in candidates
            )
        if mutation == "missing_reference":
            selected["citation_ids"] = selected["citation_ids"][:-1]
        elif mutation == "changed_reference":
            selected["citation_ids"] = sorted(
                [
                    *selected["citation_ids"][:-1],
                    "provider_" + "f" * 64,
                ]
            )
        else:
            selected["candidate_id"] = "alternative_" + "f" * 64

        def digest(value):
            return hashlib.sha256(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()

        selected["qualification_candidate_id"] = "qualification_candidate_" + digest(
            {
                **{
                    key: selected[key]
                    for key in (
                        "action_id",
                        "candidate_id",
                        "citation_ids",
                        "combination_ids",
                        "rule_id",
                    )
                },
                "catalogue_sha256": original["catalogue_sha256"],
            }
        )
        selected["outcome_id"] = "shadow_outcome_" + digest(
            {key: value for key, value in selected.items() if key != "outcome_id"}
        )
        original["outcome_set_id"] = "shadow_outcome_set_" + digest(
            {key: value for key, value in original.items() if key != "outcome_set_id"}
        )
        forged_outcome = ShadowOutcomeSet.from_json(
            json.dumps(
                original, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
        )
        arguments = {
            key: value
            for key, value in raw_decision.items()
            if key
            not in {"decision_id", "idempotency_digest", "origin", "schema_version"}
        }
        arguments.update(
            candidate_id=selected["candidate_id"],
            source_outcome_id=selected["outcome_id"],
        )
        if field == "title":
            from mechanical_librarian.domain.automatic_decision import (
                audiobook_title_candidate_bundle,
            )

            arguments["bundle_id"] = audiobook_title_candidate_bundle(
                next(
                    member
                    for member in forged_outcome.outcomes
                    if member.destination == "title"
                )
            )
        forged = AutomaticDecision.create(**arguments)
        forged = AutomaticDecision.from_json(forged.to_json())
        with pytest.raises(FieldEffectError, match="authority_binding"):
            _automatic_decision_value(forged, case, report, proposal, forged_outcome)
        with monkeypatch.context() as forged_snapshot:
            forged_snapshot.setattr(
                repository._review_projections,
                "get_review_case_decisions",
                lambda _: (forged.to_json(),),
            )
            forged_snapshot.setattr(
                repository._review_projections,
                "_automatic_source_outcome_documents",
                lambda _: (forged_outcome.to_json(),),
            )
            with pytest.raises(ReviewWebError, match="unavailable"):
                inspector.run(case_id)
        with pytest.raises(
            sqlite3.IntegrityError, match="shadow decision outcomes are append-only"
        ):
            connection.execute(
                "UPDATE shadow_decision_outcomes SET document=? WHERE outcome_set_id=?",
                (forged_outcome.to_json(), outcome.outcome_set_id),
            )
        connection.rollback()
        assert (
            connection.execute("SELECT COUNT(*) FROM field_effects").fetchone()[0] == 0
        )
    with closing(open_repository(database)) as reopened:
        assert raw_decision in [
            json.loads(document)
            for document in reopened.get_review_case_decisions(case_id)
        ]


@pytest.mark.parametrize("option", ("canonical", "preserve"))
def test_complete_source_authors_add_keeps_every_credit_through_guarded_effect(
    effects, monkeypatch, option
):
    report = synthetic_audiobook_bibliographic_report(
        source_authors=("Example Author", "Second Author")
    )
    identity = f"contributor_names:{option}:audiobook_authors_add"
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS | {identity},
    )
    case_id, _ = publish_audiobook_preview(
        effects.repository,
        report,
        ("contributor_names",),
        selected_options={"contributor_names": option},
    )
    outcome = json.loads(effects.repository.persist_shadow_outcome(case_id, 4))
    selected = next(
        member for member in outcome["outcomes"] if member["destination"] == "authors"
    )
    assert set(selected["citation_ids"]) == {
        claim.reference
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}
        and claim.state is FieldState.VALID
    }
    documents = effects.repository.get_review_case_decisions(case_id)
    decision = next(
        AutomaticDecision.from_json(document)
        for document in documents
        if json.loads(document)["destination"] == "authors"
    )
    assert decision.policy_option == f"{option}:audiobook_authors_add"
    authority = PersistedFieldEffectAuthority(effects.repository)
    snapshot = authority.resolve(decision)
    assert snapshot.pre_value == ["Example Author"]
    assert snapshot.decision_value == {
        "kind": "list",
        "value": ["Example Author", "Second Author"],
    }
    effects.repository.grant_field_effect_consents(
        "primary", ("audio",), ("authors",), 5
    )
    service = effects.persisted_service(effects.repository, effects.target, authority)
    operation = service.plan(
        decision,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination="authors",
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    effects.target.seed(operation.plan)
    result = service.apply(operation.plan.plan_id)
    assert result.state == service.apply(operation.plan.plan_id).state == "verified"
    assert effects.target._values[("primary", "audio", "item-1", "authors")] == [
        "Example Author",
        "Second Author",
    ]
    effects.repository.close()
    effects.repository = open_repository(effects.database_path)
    assert effects.repository.get_review_case_decisions(case_id) == documents
    assert (
        effects.repository.get_field_effect(operation.plan.plan_id).to_data()
        == result.to_data()
    )
    assert (
        effects.repository._connection.execute(
            "SELECT COUNT(*) FROM field_effects"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("source", ("2025", "2025-02", "2025-02-03"))
def test_original_work_date_precision_reaches_current_guarded_effect(effects, source):
    from tests.support.audiobook_reports import synthetic_audiobook_original_date_report

    report = synthetic_audiobook_original_date_report(source=source)
    decisions = _automatic_decisions(
        effects, report, "date_precision", "full_date", qualified=True
    )
    decision = next(
        value for value in decisions if value.destination == "publishedDate"
    )
    authority = PersistedFieldEffectAuthority(effects.repository)
    snapshot = authority.resolve(decision)
    assert snapshot.decision_value == {"kind": "text", "value": source}
    effects.repository.grant_field_effect_consents(
        "primary", ("audio",), ("publishedDate",), 5
    )
    service = effects.persisted_service(effects.repository, effects.target, authority)
    operation = service.plan(
        decision,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination="publishedDate",
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    effects.target.seed(operation.plan)
    result = service.apply(operation.plan.plan_id)
    assert result.state == "verified"
    assert service.apply(operation.plan.plan_id).state == "verified"
    assert result.plan.desired_value == source
    effects.repository.close()
    effects.repository = open_repository(effects.database_path, create=False)
    assert (
        effects.repository.get_field_effect(operation.plan.plan_id).to_data()
        == result.to_data()
    )
    assert PersistedFieldEffectAuthority(effects.repository).resolve(
        decision
    ).decision_value == {
        "kind": "text",
        "value": source,
    }


def test_isbn_refusal_history_pagination_filter_and_reader_cache(tmp_path):
    from mechanical_librarian.domain.readable_activity import ISBN_REFUSAL_REASONS
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
    from tests.support.audiobook_reports import (
        admitted_audiobook_report,
        audio_local_claims,
        synthetic_audiobook_isbn_reason_report,
    )

    shapes = ("absent", "unavailable", "malformed", "conflict", "unassociated")
    base_reports = {
        shape: synthetic_audiobook_isbn_reason_report(shape) for shape in shapes
    }
    unavailable = base_reports["unavailable"]
    failed = tuple(
        provider
        for provider in unavailable.providers
        if provider.provider == "audnexus"
    )
    required = {ref for provider in failed for ref in provider.input_references}
    witnesses = tuple(
        claim for claim in unavailable.local_claims if claim.reference in required
    )
    base_reports["unavailable"] = admitted_audiobook_report(
        (*audio_local_claims(), *witnesses), additional_providers=failed
    )
    path = tmp_path / "isbn-history.sqlite"
    repository = open_repository(path)
    reader = None
    expected_ids = []
    try:
        for index in range(30):
            raw = base_reports[shapes[index % 5]]
            report = MetadataReport.create(
                target=replace(raw.target, item=f"item-{index}"),
                file=raw.file,
                local_claims=raw.local_claims,
                local_reasons=raw.local_reasons,
                providers=raw.providers,
                audio=raw.audio,
            )
            if index == 0:
                case_id, resolved = publish_audiobook_preview(
                    repository,
                    report,
                    ("isbn_form",),
                    selected_options={"isbn_form": "preserve_exact"},
                )
            else:
                proposal = evaluate_report(
                    report, resolved.resolved_id, human_review_destinations(resolved)
                )
                repository.publish(report, lambda: True)
                repository.publish_proposal(proposal)
                case_id = json.loads(repository.admit_review_case(proposal))[
                    "review_case_id"
                ]
            document = repository.persist_shadow_outcome(case_id, 10 + index)
            outcome = ShadowOutcomeSet.from_json(document)
            expected_ids.append(outcome.outcome_set_id + ":isbn")
            if index == 4:
                reader = open_repository(
                    path, create=False, authority=repository.authority
                )
                reader.configure_activity_page_reader()
                service = ContinuousGuardedApplication(
                    reader,
                    {},
                    {},
                    PersistedFieldEffectAuthority(reader),
                    (("primary", "audio"),),
                    lambda: 2000,
                    lambda *_: None,
                    activity_only=True,
                    activity_snapshot=reader.field_effect_activity_snapshot,
                )
                assert service.activity(event_type="metadata")["ledger"]["total"] == 5

        first = service.activity(event_type="metadata", page=1, page_size=25)
        second = service.activity(event_type="metadata", page=2, page_size=25)
        assert first["ledger"]["total"] == second["ledger"]["total"] == 30
        first_events, second_events = first["events"], second["events"]
        assert type(first_events) is list and type(second_events) is list
        assert [row["event_id"] for row in first_events + second_events] == list(
            reversed(expected_ids)
        )
        assert len(first["events"]) == 25 and len(second["events"]) == 5
        assert service.activity(event_type="unknown")["ledger"]["total"] == 0
        for label in ISBN_REFUSAL_REASONS.values():
            matched = service.activity(event_type="metadata", search=label)
            assert matched["ledger"]["total"] == len(matched["events"]) == 6
        assert repository.persist_shadow_outcome(case_id, 99) == document
        assert service.activity(event_type="metadata", page=1, page_size=25) == first
        assert repository.pending_field_effect_decision_counts() == ()
        assert (
            repository._connection.execute(
                "SELECT COUNT(*) FROM field_effects"
            ).fetchone()[0]
            == 0
        )
    finally:
        if reader is not None:
            reader.close()
        repository.close()
    with closing(open_repository(path)) as reopened:
        page = reopened.field_effect_activity_page(
            search=None, event_type="metadata", occurred_after=0, page=1, page_size=50
        )
        assert [row["event_id"] for row in page["events"]] == list(
            reversed(expected_ids)
        )


@pytest.mark.parametrize("preserve", (False, True))
def test_actual_audiobook_author_activity_names_the_policy_without_operation_identity(
    tmp_path, preserve
):
    names = ("Example Author", "Second Author")
    report = synthetic_audiobook_bibliographic_report(
        source_authors=names, target_authors=names if preserve else names[:1]
    )
    path = tmp_path / "state.sqlite"
    repository = open_repository(path)
    case_id, _ = publish_audiobook_preview(
        repository,
        report,
        ("contributor_names",),
        selected_options={"contributor_names": "canonical"},
    )
    repository.persist_shadow_outcome(case_id, 4)
    documents = repository.get_review_case_decisions(case_id)
    assert len(documents) == 1
    decision = AutomaticDecision.from_json(documents[0])
    assert decision.outcome == ("keep_current" if preserve else "candidate")
    assert decision.policy_option == (
        "canonical:audiobook_authors_preserve"
        if preserve
        else "canonical:audiobook_authors_add"
    )
    repository.close()
    repository = open_repository(path)
    try:
        lane = ContinuousGuardedApplication(
            repository,
            {},
            {},
            PersistedFieldEffectAuthority(repository),
            (("primary", "audio"),),
            lambda: 6,
            lambda *_: None,
            activity_only=True,
        )
        event = next(
            item
            for item in lane.activity()["events"]
            if item["event_id"] == decision.decision_id
        )
        assert event["change_detail"] == (
            "Kept Authors as Example Author, Second Author"
            if preserve
            else "Chose a Authors value automatically"
        )
        facts = {item["label"]: item["value"] for item in event["evidence"]}
        assert facts["Policy setting"] == "Contributor names: Canonical form"
        assert facts["New value"] == "Example Author, Second Author"
        assert repository.get_review_case_decisions(case_id) == documents
    finally:
        repository.close()


@pytest.mark.parametrize("state", ("claimed", "applying", "reconciling", "verified"))
def test_audiobook_pair_current_override_and_owner_history(
    audiobook_pair, tmp_path, state
):
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase

    pair = audiobook_pair
    operation = pair.plan()
    if state == "verified":
        assert pair.service.apply(operation.plan.plan_id).state == "verified"
        _paired_owner_keep(pair, "subtitle")
    else:
        pair.repo.claim_field_effect(
            operation.plan.plan_id, "planned", "1" * 64, pair.env.clock()
        )
        if state in {"applying", "reconciling"}:
            pair.repo.transition_field_effect(
                operation.plan.plan_id, "claimed", "applying", pair.env.clock()
            )
        if state == "reconciling":
            pair.repo.schedule_field_effect_reconciliation(
                operation.plan.plan_id, "applying", pair.env.clock()
            )
    pair.reopen()
    assert pair.repo.get_field_effect(operation.plan.plan_id).state == state
    inspector = InspectCurrentReviewCase(pair.repo)
    if state == "verified":
        event = next(
            item
            for item in inspector.history(pair.case_id)["items"]
            if item.get("destination") == "subtitle" and item.get("actor") == "owner"
        )
        assert event["previous_value"] == {"kind": "text", "value": "A Tale of Courage"}
    else:
        fields = {
            item["destination"]: item for item in inspector.run(pair.case_id)["fields"]
        }
        assert (
            fields["title"]["decision_actions"]
            == fields["subtitle"]["decision_actions"]
            == ["keep_current", "accept"]
        )
        assert not fields["title"]["editable"] and not fields["subtitle"]["editable"]
    assert len(pair.patches()) == (1 if state == "verified" else 0)

    pair.repo.close()
    pair.repo = open_repository(tmp_path / "state.sqlite", cross_thread=True)
    password = tmp_path / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        pair.repo,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
        ),
        clock=pair.env.clock,
    )
    with TestClient(app, base_url="https://review.test") as client:
        assert (
            client.post(
                "/api/v1/session/login",
                json={"password": "correct horse"},
                headers={"Origin": "https://review.test"},
            ).status_code
            == 200
        )
        if state == "verified":
            response = client.get(f"/api/v1/review-cases/{pair.case_id}/history")
            assert response.status_code == 200
            api_event = next(
                item
                for item in response.json()["items"]
                if item.get("destination") == "subtitle"
                and item.get("actor") == "owner"
            )
            assert api_event["previous_value"] == event["previous_value"]
        else:
            response = client.get(f"/api/v1/review-cases/{pair.case_id}")
            assert response.status_code == 200
            api_fields = {
                item["destination"]: item for item in response.json()["fields"]
            }
            assert (
                not api_fields["title"]["editable"]
                and not api_fields["subtitle"]["editable"]
            )


def test_admitted_effect_releases_database_owner_during_target_io(effects):
    from mechanical_librarian.application.operation_admission import (
        OperationAdmission as Admission,
    )
    from mechanical_librarian.domain.human_decision import HumanDecisionError
    from tests.support.evidence_led_titles import isbn_title_report, publish_title_case

    env = effects
    admission = Admission()
    lock = threading.RLock()
    repo = env.repository
    repo.bind_operation_admission(admission, lock)
    release = threading.Event()
    entered = threading.Event()
    errors = []
    results = []
    target_calls = []
    thread = None
    try:
        case_id, _ = publish_title_case(env.repository, isbn_title_report())
        base = isbn_title_report()
        other = MetadataReport.create(
            target=replace(base.target, item="other-work"),
            file=replace(base.file, identity="2" * 64),
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            identifier_evidence=base.identifier_evidence,
        )
        policy = repo.current_resolved_metadata_policy(other.target.library)
        proposal = evaluate_report(other, policy.resolved_id, dict(policy.destinations))
        repo.publish(other, lambda: True)
        repo.publish_proposal(proposal)
        other_id = json.loads(repo.admit_review_case(proposal))["review_case_id"]
        other_case = ReviewCase.create_from_data(
            json.loads(repo.get_review_case(other_id))["case"]
        )
        raw = env.repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='title'"
        ).fetchone()[0]
        source = AutomaticDecision.from_json(raw)
        authority = PersistedFieldEffectAuthority(repo)
        snapshot = authority.resolve(source)
        repo.grant_field_effect_consents(
            snapshot.connection, (snapshot.library,), ("title",), 5
        )
        service = GuardedFieldApplication(
            repo,
            env.target,
            env.backup,
            env.capabilities,
            authority,
            env.dependencies,
            PersistedEffectConsent(repo),
            env.recovery,
            env.reevaluation,
            env.clock,
        )
        operation = service.plan(
            source,
            connection=snapshot.connection,
            library=snapshot.library,
            work_id=snapshot.work_id,
            destination="title",
            pre_value=snapshot.pre_value,
            desired_value=snapshot.decision_value,
            target_fingerprint=snapshot.target_fingerprint,
            target_version=snapshot.target_version,
            dependency_digests=snapshot.dependency_digests,
            capability_digest=CAPABILITY,
        )
        assert operation
        plan = operation.plan
        env.target.seed(plan)
        next_source = decision("subtitle", envelope("subtitle", "new"), "f")
        env.authority.bind(
            next_source,
            connection="primary",
            library=snapshot.library,
            work_id="next-work",
            target_version="version-1",
        )
        env.consent.allow(
            connection="primary", library=snapshot.library, destination="subtitle"
        )
        next_operation = env.service.plan(
            next_source,
            connection="primary",
            library=snapshot.library,
            work_id="next-work",
            destination="subtitle",
            pre_value="old",
            desired_value=envelope("subtitle", "new"),
            target_fingerprint="d" * 64,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        )
        assert next_operation
        env.target.seed(next_operation.plan)
        env.service._repository = repo
        case = ReviewCase.create_from_data(
            json.loads(repo.get_review_case(case_id))["case"]
        )

        def command(destination, key, case=case):
            proposal = next(
                (p for p in case.proposals if p["destination"] == destination)
            )
            return HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=destination,
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key=key,
                occurred_at=env.clock(),
            )

        replacement = command("title", "active-effect-title-refusal")
        repo.claim_field_effect(plan.plan_id, "planned", "c" * 64, env.clock())
        try:
            repo.record_human_decision(replacement)
            raise AssertionError("claimed replacement accepted")
        except HumanDecisionError as e:
            assert e.code == "already_decided"
        original_apply = env.target.apply
        original_read = env.target.read

        def held_apply(p):
            assert not admission.held
            assert not lock._is_owned()
            assert not env.repository._connection.in_transaction
            target_calls.append("apply_enter")
            entered.set()
            assert release.wait(5)
            result = original_apply(p)
            target_calls.append("apply_return")
            return result

        def observed_read(p):
            assert not admission.held
            assert not lock._is_owned()
            target_calls.append("read")
            return original_read(p)

        env.target.apply = held_apply
        env.target.read = observed_read

        def worker():
            try:
                with admission.classify("background"):
                    results.append(service.apply(plan.plan_id))
            except BaseException as e:
                errors.append(repr(e))

        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(5)
        assert repo.get_field_effect(plan.plan_id).state == "applying"
        try:
            repo.record_human_decision(replacement)
            raise AssertionError("applying replacement accepted")
        except HumanDecisionError as e:
            assert e.code == "already_decided"
        unrelated = command("title", "unrelated-foreground-keep", other_case)
        repo.record_human_decision(unrelated)
        assert any(
            (
                json.loads(x)["decision_id"] == unrelated.decision_id
                for x in repo.get_review_case_decisions(other_id)
            )
        )
        service.set_paused(True)
        try:
            env.service.apply(next_operation.plan.plan_id)
            raise AssertionError("paused next claim accepted")
        except GuardedApplicationError as e:
            assert e.code == "paused"
        assert repo.get_field_effect(next_operation.plan.plan_id).state == "planned"
        service.set_paused(False)
        repo.revoke_field_effect_consent(
            next_operation.plan.consent.consent_id, env.clock()
        )
        assert env.service.apply(next_operation.plan.plan_id).state == "superseded"
        assert not release.is_set()
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert not errors, errors
        assert len(results) == 1 and results[0].state == "verified"
        assert service.apply(plan.plan_id).state == "verified"
        events = [
            json.loads(x) for x in repo.field_effect_event_documents(plan.plan_id)
        ]
        assert sum((x["state"] == "verified" for x in events)) == 1, events
        assert len(env.target._effects) == 1
        assert target_calls[target_calls.index("apply_return") + 1 :] == ["read"], (
            target_calls
        )
    finally:
        release.set()
        if thread is not None:
            thread.join(5)
            assert not thread.is_alive()


def test_paired_post_write_readback_projects_unconfirmed(audiobook_pair):
    op = audiobook_pair.plan()
    audiobook_pair.case.paired_mode = "partial"
    result = audiobook_pair.service.apply(op.plan.plan_id)
    assert (result.state, result.failure) == ("reconciling", "readback")
    page = audiobook_pair.repo.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=25
    )
    event = next(event for event in page["events"] if event["state"] == "reconciling")
    readable_activity_event(event, audiobook_pair.report)
    assert event["change"] == "Paired title and subtitle change unconfirmed"
    assert "Both fields have not been confirmed" in str(event["evidence"])
    assert "stopped" not in event["change"]


@pytest.mark.parametrize("audio", (False, True))
@pytest.mark.parametrize(
    "option, expected", (("preserve", "en"), ("display_name", "English"))
)
@pytest.mark.parametrize("current", (None, "UND"))
def test_observed_language_reaches_guarded_write_and_readback(
    effects, audio, option, expected, current
):
    from tests.support.language_reports import language_report

    report = language_report(("English", "en", "und"), current, audio=audio)
    decision = next(
        value
        for value in _automatic_decisions(
            effects, report, "language_rendering", option, qualified=True
        )
        if value.destination == "language"
    )
    authority = PersistedFieldEffectAuthority(effects.repository)
    snapshot = authority.resolve(decision)
    assert snapshot.pre_value == current
    assert snapshot.decision_value == {"kind": "text", "value": expected}
    effects.repository.grant_field_effect_consents(
        "primary", (report.target.library,), ("language",), 5
    )
    service = effects.persisted_service(effects.repository, effects.target, authority)
    arguments = dict(
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination="language",
        pre_value=snapshot.pre_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    with pytest.raises(GuardedApplicationError, match="authority_binding"):
        service.plan(
            decision, desired_value={"kind": "text", "value": "eng"}, **arguments
        )
    operation = service.plan(
        decision, desired_value=snapshot.decision_value, **arguments
    )
    assert operation is not None
    effects.target.seed(operation.plan)
    result = service.apply(operation.plan.plan_id)
    assert result.state == "verified"
    assert result.plan.desired_value == expected
    assert (
        effects.target._values[("primary", report.target.library, "item-1", "language")]
        == expected
    )
    assert service.apply(operation.plan.plan_id).state == "verified"
    effects.repository.close()
    effects.repository = open_repository(effects.database_path, create=False)
    assert (
        effects.repository.get_field_effect(operation.plan.plan_id).to_data()
        == result.to_data()
    )
    assert PersistedFieldEffectAuthority(effects.repository).resolve(
        decision
    ).decision_value == {"kind": "text", "value": expected}


@pytest.mark.parametrize("guard", [None, "backup", "target", "consent", "readback"])
def test_ebook_fill_uses_guarded_persisted_effect(effects, guard):
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.domain.automatic_decision import AutomaticDecision
    from tests.support.ebook_synopsis import (
        publish_synopsis_preview,
        synopsis_report,
    )

    case, _ = publish_synopsis_preview(effects.repository, synopsis_report())
    effects.repository.persist_shadow_outcome(case, 4)
    decision = next(
        AutomaticDecision.from_json(d)
        for d in effects.repository.get_review_case_decisions(case)
        if json.loads(d)["destination"] == "description"
    )
    authority = PersistedFieldEffectAuthority(effects.repository)
    snapshot = authority.resolve(decision)
    effects.repository.grant_field_effect_consents(
        snapshot.connection, (snapshot.library,), ("description",), 5
    )
    service = effects.persisted_service(effects.repository, effects.target, authority)
    result = service.plan(
        decision,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination="description",
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert result is not None
    effects.target.seed(result.plan)
    if guard == "backup":
        effects.backup.ready_value = False
    elif guard == "target":
        effects.target.drift(result.plan, "Owner edited synopsis.")
    elif guard == "consent":
        effects.repository.revoke_field_effect_consent(
            result.plan.consent.consent_id, effects.clock()
        )
    elif guard == "readback":
        effects.target.fail_read_after_apply_for.add(result.plan.plan_id)
    before = dict(effects.target._values)
    applied = service.apply(result.plan.plan_id)
    if guard in {"backup", "target", "consent"}:
        assert applied.state == ("superseded" if guard == "consent" else "stale")
        assert effects.target._values == before
    elif guard == "readback":
        assert (applied.state, applied.failure) == ("reconciling", "target_failure")
    else:
        assert applied.state == "verified"
        assert service.apply(result.plan.plan_id).state == "verified"
        assert (
            effects.target.read(result.plan).value == "A voyage reveals a hidden city."
        )


@pytest.mark.parametrize("option", ("preserve", "title_and_subtitle"))
def test_scalar_incumbent_title_is_one_durable_no_change(effects, option):
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        InspectCurrentReviewCase,
    )
    from tests.support.proposal_evidence import (
        _report,
        consensus_local_title,
        consensus_provider_title,
    )

    report = _report(
        title="Example Work",
        local_claims=(consensus_local_title("Example Work"),),
        providers=(
            consensus_provider_title("hardcover", "Example Work"),
            consensus_provider_title("open_library", "Other Work"),
        ),
    )
    decisions = _automatic_decisions(
        effects, report, "title_layout", option, qualified=True
    )
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.destination == "title" and decision.outcome == "keep_current"
    case_id = decision.review_case_id
    document = effects.repository.persist_shadow_outcome(case_id, 4)
    selected = next(
        item
        for item in json.loads(document)["outcomes"]
        if item["destination"] == "title"
    )
    assert selected["disposition"] == "keep_current"
    assert selected["reason_code"] == "unique_supported_preservation"
    assert effects.repository.automatic_safety_state().permits("title_layout")
    authority = PersistedFieldEffectAuthority(effects.repository)
    lane = ContinuousGuardedApplication(
        effects.repository,
        {("primary", "ebooks"): effects.service},
        {("primary", "ebooks"): effects.capabilities},
        authority,
        (("primary", "ebooks"),),
        effects.clock,
        lambda *_: None,
    )
    with (
        patch.object(
            authority,
            "resolve",
            side_effect=AssertionError("no-change reached effect guards"),
        ),
        patch.object(
            effects.target, "read", side_effect=AssertionError("target read")
        ) as read,
        patch.object(
            effects.target, "apply", side_effect=AssertionError("target write")
        ) as apply,
    ):
        assert lane.schedule() == lane.schedule() == 0
        read.assert_not_called()
        apply.assert_not_called()
    assert effects.target._values == {} and effects.target._effects == {}
    assert effects.repository.pending_field_effect_decision_documents(100) == ()
    assert effects.repository._connection.execute(
        "SELECT COUNT(*) FROM field_effects"
    ).fetchone() == (0,)
    events = effects.repository._connection.execute(
        "SELECT decision_id,destination,event_id FROM field_effect_no_changes"
    ).fetchall()
    assert len(events) == 1 and events[0][:2] == (decision.decision_id, "title")
    effects.repository.close()
    effects.repository = open_repository(effects.database_path, create=False)
    assert effects.repository.persist_shadow_outcome(case_id, 4) == document
    assert effects.repository.get_review_case_decisions(case_id) == (
        decision.to_json(),
    )
    assert (
        effects.repository._connection.execute(
            "SELECT decision_id,destination,event_id FROM field_effect_no_changes"
        ).fetchall()
        == events
    )
    assert effects.repository.automatic_safety_state().permits("title_layout")
    detail = InspectCurrentReviewCase(effects.repository).run(case_id)
    title = next(item for item in detail["fields"] if item["destination"] == "title")
    assert title["current"]["value"] == "Example Work"
    assert title["automatic_decision"]["outcome"] == "keep_current"
    assert not title["needs_attention"]
    library = BrowseReviewLibrary(effects.repository).library(
        lens="all", search=None, limit=25, cursor=None
    )
    assert any(item["review_case_id"] == case_id for item in library["items"])
    activity = effects.repository.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=25
    )
    unchanged = [
        event for event in activity["events"] if event["state"] == "no_change_required"
    ]
    assert len(unchanged) == 1
    assert unchanged[0]["destination"] == "title"


def test_activity_validates_each_joined_report_once_for_repeated_automatic_events(
    effects: _EffectEnvironment,
) -> None:
    report = _target_work_report(
        (
            (
                "catalogue",
                "hardcover",
                (_target_work_candidate("catalogue", "edition-1", "work-1"),),
                True,
            ),
        )
    )
    decisions = _automatic_decisions(
        effects, report, "description_fill", "safe_plain_text", qualified=True
    )
    assert decisions
    with patch.object(
        MetadataReport, "from_json", wraps=MetadataReport.from_json
    ) as parse:
        page = effects.repository.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )
    documents = page["report_documents"]
    assert documents == {report.report_id: report.to_json()}
    assert parse.call_args_list == [((report.to_json(),), {})]
    assert page["validated_reports"] == {report.report_id: report}
    assert {event["record"]["source_kind"] for event in page["events"]} >= {
        "automatic_decision",
        "policy_transition",
    }


def test_activity_report_scalars_are_selected_page_bounded_and_search_still_validates_history(
    effects: _EffectEnvironment,
) -> None:
    from tests.support.audiobook_reports import admitted_audiobook_report

    _automatic_decisions(
        effects,
        admitted_audiobook_report(item="item-0"),
        "title_layout",
        "title_and_subtitle",
        qualified=True,
    )
    resolved = effects.repository.current_resolved_metadata_policy("audio")
    assert resolved is not None
    for index in range(1, 30):
        report = admitted_audiobook_report(item=f"item-{index}")
        proposal = evaluate_report(report, resolved.resolved_id)
        effects.repository.publish(report, lambda: True)
        effects.repository.publish_proposal(proposal)
        case_id = json.loads(effects.repository.admit_review_case(proposal))[
            "review_case_id"
        ]
        effects.repository.persist_shadow_outcome(case_id, 4)
    assert effects.repository._connection.execute(
        "SELECT count(*) FROM metadata_reports"
    ).fetchone() == (30,)
    assert (
        effects.repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions"
        ).fetchone()[0]
        >= 30
    )
    query = dict(
        search=None, event_type="metadata", occurred_after=0, page=1, page_size=25
    )
    page = effects.repository.field_effect_activity_page(**query)
    selected = set(cast("dict[str, str]", page["report_documents"]))
    documents = dict(
        effects.repository._connection.execute(
            "SELECT report_id,document FROM metadata_reports"
        )
    )
    unrelated = next(
        document for key, document in documents.items() if key not in selected
    )
    scalar_calls: list[str] = []
    unrelated_calls: list[str] = []
    with sqlite3.connect(":memory:") as native:

        def extract(document: str, path: str):
            if document == unrelated:
                unrelated_calls.append(path)
                raise ValueError("unselected report scalar reached")
            if document in documents.values():
                scalar_calls.append(path)
            return native.execute(
                "SELECT json_extract(?,?)", (document, path)
            ).fetchone()[0]

        # Match the builtin variadic arity so SQLite 3.40 also invalidates an
        # already prepared statement instead of retaining its builtin callback.
        effects.repository._connection.create_function("json_extract", -1, extract)
        # This private cache is the optimization boundary under test: force the SQL path.
        projection = effects.repository._field_effect_activity
        projection._activity_page_cache.clear()
        projection._activity_page_cache_bytes = 0
        repeated = effects.repository.field_effect_activity_page(**query)
        assert repeated == page
        assert unrelated_calls == []
        assert 0 < len(scalar_calls) <= 4 * len(page["events"])
        with pytest.raises(FieldEffectError, match="^persistence$"):
            effects.repository.field_effect_activity_page(
                search="no matching text",
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=25,
            )
    assert unrelated_calls


def test_activity_fresh_and_cached_public_projections_match_with_transient_reports(
    tmp_path: Path,
) -> None:
    from tests.support.activity_seed import seed_activity

    seeded = seed_activity(tmp_path)
    repository = seeded.repository
    reader = open_repository(
        tmp_path / "activity.sqlite",
        create=False,
        authority=repository.authority,
        cross_thread=True,
    )
    reader.configure_activity_page_reader()
    application = ContinuousGuardedApplication(
        reader,
        {},
        {},
        PersistedFieldEffectAuthority(reader),
        (("primary", "books"),),
        seeded.reader_now,
        lambda *_: None,
        activity_only=True,
        activity_snapshot=reader.field_effect_activity_snapshot,
    )
    try:
        first = application.activity(page_size=100)
        second = application.activity(page_size=100)
        assert first == second
        assert len(first["events"]) > 25
        assert {event["category"] for event in first["events"]} >= {
            "owner",
            "automatic",
            "policy",
        }
        assert all(
            "validated_reports" not in entry[4]
            for entry in reader._field_effect_activity._activity_page_cache.values()
        )
    finally:
        reader.close()
        repository.close()


@pytest.mark.parametrize("intervening_commit", [False, True])
def test_permission_readiness_binds_fresh_check_to_its_snapshot(
    effects: _EffectEnvironment,
    intervening_commit: bool,
) -> None:
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    reader.configure_activity_page_reader()
    observed = []

    def before_snapshot(statement):
        if statement == "BEGIN" and intervening_commit:
            effects.repository.set_field_effect_paused(True, effects.clock())

    reader._connection.set_trace_callback(before_snapshot)
    try:

        def project(ready):
            assert reader._connection.in_transaction
            observed.append(ready)
            return {"backup_ready": ready}

        assert reader.field_effect_permission_readiness_snapshot(project) == {
            "backup_ready": None if intervening_commit else True,
        }
        assert observed == [None if intervening_commit else True]
        assert not reader._connection.in_transaction
        reader._connection.set_trace_callback(None)
        assert reader.field_effect_permission_readiness_snapshot(project) == {
            "backup_ready": True,
        }
        with pytest.raises(RuntimeError, match="projection"):
            reader.field_effect_permission_readiness_snapshot(
                lambda _: (_ for _ in ()).throw(RuntimeError("projection"))
            )
        assert not reader._connection.in_transaction
    finally:
        reader.close()


@pytest.mark.parametrize("failed_check", [False, True])
def test_permission_readiness_never_reuses_a_previous_success(
    effects: _EffectEnvironment,
    monkeypatch,
    failed_check: bool,
) -> None:
    reader = open_repository(
        effects.database_path,
        create=False,
        authority=effects.repository.authority,
        cross_thread=True,
    )
    try:
        assert reader.field_effect_permission_readiness_snapshot(
            lambda ready: {"ready": ready}
        ) == {"ready": True}
        closed = []

        class Verifier:
            def execute(self, statement):
                assert statement == "PRAGMA quick_check"
                if failed_check:
                    raise sqlite3.DatabaseError("synthetic unavailable check")
                return self

            def fetchone(self):
                return ("synthetic inconsistency",)

            def close(self):
                closed.append(True)

        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: Verifier())
        assert reader.field_effect_permission_readiness_snapshot(
            lambda ready: {"ready": ready}
        ) == {"ready": None if failed_check else False}
        assert closed == [True]
        assert not reader._connection.in_transaction
    finally:
        reader.close()


def test_retained_audiobook_cover_plan_reopens_but_cannot_apply(tmp_path, effects):
    from mechanical_librarian.domain.cover_automatic_decision import (
        CoverAutomaticDecision,
    )
    from tests.support.metadata_report_schemas import retained_report_as_current

    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "support/retained_qualification_predecessors/cover.json"
        ).read_text()
    )
    operation = FieldEffectOperation.from_json(
        json.dumps(fixture["operation"], sort_keys=True, separators=(",", ":"))
    )
    path = tmp_path / "state.sqlite"
    with closing(open_repository(path)) as source:
        report = retained_report_as_current(fixture["report"])
        case_id, _ = publish_audiobook_preview(
            source,
            report,
            ("cover_order",),
            selected_options={"cover_order": "replace_if_strictly_better"},
        )
        assert case_id != fixture["consideration"]["review_case_id"]
        historical_decision = CoverAutomaticDecision.from_json(fixture["decision"])
        assert historical_decision.to_json() == fixture["decision"]
        assert (
            historical_decision.review_case_id
            == operation.plan.authority.review_case_id
        )
        source.record_field_effect(operation)
        source.close()
        repository = open_repository(path, create=False)
        try:
            repository.bind_current_cover_policy(
                fixture["consideration"]["policy_reference"]
            )
            repository.bind_current_cover_replay(
                fixture["consideration"]["replay_reference"]
            )
            assert repository.get_field_effect(operation.plan.plan_id) == operation
            assert not repository.field_effect_authority_is_current(
                operation.plan.authority
            )
            effects.target.seed(operation.plan)
            guarded = effects.persisted_service(
                repository, effects.target, PersistedFieldEffectAuthority(repository)
            )
            result = guarded.apply(operation.plan.plan_id)
            assert result.state == "stale" and result.failure == "authority"
            assert not effects.target._effects
        finally:
            repository.close()


@pytest.mark.parametrize("fresh_borrower", [False, True])
def test_activity_facts_refuse_commit_followed_by_raw_document_corruption(
    tmp_path, fresh_borrower
):
    import os

    from mechanical_librarian.adapters.persistence.activity_facts import (
        ActivityFactStore,
    )
    from tests.support.owner_responsiveness import OwnerResponsivenessFixture

    fixture = OwnerResponsivenessFixture(tmp_path, 10)
    facts = ActivityFactStore()
    reader = open_repository(
        fixture.database, create=False, authority=fixture.repository.authority
    )
    query = dict(
        search="no readable match",
        event_type="all",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    try:
        reader.configure_activity_page_reader(facts)
        reader.field_effect_activity_page(**query)
        assert facts.snapshot() is not None
        if fresh_borrower:
            reader.close()
        fixture.repository._connection.execute(
            "UPDATE metadata_policy_transitions SET updated_at=updated_at+1"
        )
        fixture.repository._connection.commit()
        document = (
            fixture.repository._connection.execute(
                "SELECT document FROM cover_consideration_events LIMIT 1"
            )
            .fetchone()[0]
            .encode()
        )
        raw = fixture.database.read_bytes()
        offset = raw.find(document)
        assert offset >= 0 and raw.find(document, offset + 1) == -1
        descriptor = os.open(fixture.database, os.O_WRONLY)
        try:
            os.pwrite(descriptor, b"!", offset)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if fresh_borrower:
            reader = open_repository(
                fixture.database, create=False, authority=fixture.repository.authority
            )
            reader.configure_activity_page_reader(facts)
        with pytest.raises(FieldEffectError, match="persistence"):
            reader.field_effect_activity_page(**query)
    finally:
        reader.close()
        fixture.close()


def test_activity_facts_witness_document_sql_type_before_nonmatching_search(tmp_path):
    from mechanical_librarian.adapters.persistence.activity_facts import (
        ActivityFactStore,
    )
    from tests.support.owner_responsiveness import OwnerResponsivenessFixture

    fixture = OwnerResponsivenessFixture(tmp_path, 1)
    reader = open_repository(
        fixture.database, create=False, authority=fixture.repository.authority
    )
    facts = ActivityFactStore()
    query = dict(
        search="no readable match",
        event_type="all",
        occurred_after=0,
        page=1,
        page_size=25,
    )
    try:
        reader.configure_activity_page_reader(facts)
        reader.field_effect_activity_page(**query)
        assert facts.snapshot() is not None
        connection = fixture.repository._connection
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(cover_consideration_events)"
            )
        ]
        values = list(
            connection.execute(
                "SELECT * FROM cover_consideration_events LIMIT 1"
            ).fetchone()
        )
        document = columns.index("document")
        values[document] = values[document].encode()
        schema = connection.execute("PRAGMA schema_version").fetchone()
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "INSERT OR REPLACE INTO cover_consideration_events VALUES("
            + ",".join("?" for _ in values)
            + ")",
            values,
        )
        connection.commit()
        assert connection.execute("PRAGMA schema_version").fetchone() == schema
        with pytest.raises(FieldEffectError, match="persistence"):
            reader.field_effect_activity_page(**query)
    finally:
        reader.close()
        fixture.close()


def test_activity_witness_encoding_distinguishes_exact_sqlite_scalar_types():
    from mechanical_librarian.adapters.persistence.activity_facts import _encoded

    connection = sqlite3.connect(":memory:")
    try:
        values = [
            connection.execute("SELECT ?", (value,)).fetchone()
            for value in (None, 1, 1.0, "1", b"1", -0.0, 0.0)
        ]
        assert values[1] == values[2]  # Python equality cannot be the witness.
        assert len({_encoded(value) for value in values}) == len(values)
    finally:
        connection.close()
