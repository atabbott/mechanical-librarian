"""SQLite proof for bounded, current-only policy impact pairs."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source


def _case_report(index: int) -> MetadataReport:
    source = _bibliographic_report(
        current_title="Current title",
        providers=(
            _source("catalogue", f"edition-{index}", titles=("Corrected title",)),
        ),
    )
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", f"item-{index:02d}", source.target.fields
        ),
        file=FileFacts(index, f"{index:064x}", f"{index + 100:064x}"),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )


def test_prior_persisted_policy_schema_is_rejected_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "state.sqlite3"
        repository = open_repository(database)
        row = repository._connection.execute(
            "SELECT policy_id,document FROM metadata_policy_drafts"
        ).fetchone()
        assert row is not None
        document = json.loads(row[1])
        document["schema_version"] = "metadata-policy-profile-v1"
        document["options"].pop("subtitle")
        document["options"].pop("series")
        document["options"].update(
            {
                "asin": "canonical_asin",
                "description_fill": "fill_when_empty",
                "genres": "catalogue",
                "isbn_form": "isbn13",
                "tags": "catalogue",
            }
        )
        document["destinations"].pop("subtitle")
        document["destinations"].pop("series")
        repository._connection.execute("DROP TRIGGER metadata_policy_drafts_no_update")
        repository._connection.execute(
            "UPDATE metadata_policy_drafts SET document=? WHERE policy_id=?",
            (json.dumps(document, sort_keys=True, separators=(",", ":")), row[0]),
        )
        repository._connection.execute(
            "CREATE TRIGGER metadata_policy_drafts_no_update BEFORE UPDATE "
            "ON metadata_policy_drafts BEGIN SELECT "
            "RAISE(ABORT,'immutable metadata policy draft'); END"
        )
        repository._connection.commit()
        repository.close()
        before = database.read_bytes()
        with pytest.raises(MetadataReportError):
            open_repository(database, create=False)
        assert before == database.read_bytes()


def test_current_pairs_are_bounded_ordered_and_read_only() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "state.sqlite3"
        repository = open_repository(database)
        for index in range(21):
            report = _case_report(index)
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
        before = database.read_bytes()
        total, pairs = repository.current_policy_impact_pairs("ebooks", 20)
        assert 21 == total
        assert 20 == len(pairs)
        assert tuple(sorted(report.target.item for report, _ in pairs)) == tuple(
            report.target.item for report, _ in pairs
        )
        assert before == database.read_bytes()
        assert (21, 21, 20) == (total, total, len(pairs))
        assert (0, ()) == repository.current_policy_impact_pairs("missing", 20)
        repository.close()
        reader = open_shadow_preview_repository(database)
        assert 21 == reader.current_unapplied_count("ebooks")
        assert (21, 20) == (
            reader.current_policy_impact_pairs("ebooks", 20)[0],
            len(reader.current_policy_impact_pairs("ebooks", 20)[1]),
        )
        reader.close()
        assert before == database.read_bytes()


def test_limit_and_malformed_rows_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "state.sqlite3"
        repository = open_repository(database)
        report = _case_report(1)
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
        assert 1 == repository.current_policy_impact_pairs("ebooks", 20)[0]
        with pytest.raises(MetadataReportError):
            repository.current_policy_impact_pairs("ebooks", 0)
        # Persisted proposals are append-only; attempted corruption cannot
        # create a partial preview row.
        with pytest.raises(sqlite3.IntegrityError):
            repository._connection.execute("UPDATE proposal_sets SET document='{}'")
        assert 1 == repository.current_policy_impact_pairs("ebooks", 20)[0]
        repository._connection.execute("DROP TRIGGER proposal_sets_no_update")
        repository._connection.execute("UPDATE proposal_sets SET document='{}'")
        repository._connection.execute(
            "CREATE TRIGGER proposal_sets_no_update BEFORE UPDATE ON "
            "proposal_sets BEGIN SELECT RAISE(ABORT,'immutable proposal set'); END"
        )
        repository._connection.commit()
        with pytest.raises(MetadataReportError):
            repository.current_policy_impact_pairs("ebooks", 20)
        assert not repository._connection.in_transaction
        repository.close()


def test_tags_edit_compares_both_media_on_the_same_captured_inputs(
    tmp_path, monkeypatch
) -> None:
    from mechanical_librarian.application.metadata_policy import preview_policy
    from mechanical_librarian.domain.metadata_policy import (
        DEFAULT_OPTIONS,
        OPTION_CATALOGUE,
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from tests.support.audiobook_reports import admitted_audiobook_report
    from tests.support.ebook_synopsis import synopsis_report

    database = tmp_path / "state.sqlite"
    repository = open_repository(database)
    reports = (admitted_audiobook_report(), synopsis_report())
    libraries = tuple(sorted(report.target.library for report in reports))
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations["asin"] = "automatic"
    active = MetadataPolicyProfile.create("Active", dict(DEFAULT_OPTIONS), destinations)
    resolutions = {library: resolve_policy(active, library) for library in libraries}
    repository.create_metadata_policy_draft(active.canonical_json)
    repository.activate_metadata_policy(
        active.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        tuple(
            (library, resolved.resolved_id, resolved.canonical_json)
            for library, resolved in resolutions.items()
        ),
        1,
    )
    for report in reports:
        resolved = resolutions[report.target.library]
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
    repository.close()
    before = database.read_bytes()
    reader = open_shadow_preview_repository(database)
    draft = MetadataPolicyProfile.create(
        "Tags draft",
        dict(active.options) | {"tags": "fill_empty"},
        destinations | {"tags": "automatic"},
    )
    from mechanical_librarian.application import metadata_policy as policy_application

    compilation_reads = []

    def compile_after_read(*args, **kwargs):
        assert not reader._connection.in_transaction
        compilation_reads.append(args[0].report_id)
        return evaluate_report(*args, **kwargs)

    monkeypatch.setattr(policy_application, "evaluate_report", compile_after_read)
    preview = preview_policy(draft, libraries, {}, reader)
    assert sorted(compilation_reads) == sorted(
        report.report_id for report in reports for _ in range(2)
    )
    comparisons = {
        case.media_kind: case
        for impact in preview.libraries
        for case in impact.comparisons
    }
    assert set(comparisons) == {"ebook", "audiobook"}
    asin = next(
        field
        for field in comparisons["audiobook"].fields
        if field.draft.field == "asin"
    )
    assert asin.active.current_value is None
    assert asin.active.desired_value == asin.draft.desired_value
    assert asin.draft.desired_value is not None
    assert not asin.changed
    tags = next(
        field for field in comparisons["ebook"].fields if field.draft.field == "tags"
    )
    assert tags.active.reason == "policy_off"
    assert tags.draft.desired_value == ("Exploration",)
    assert tags.changed
    unchanged = preview_policy(active, libraries, {}, reader)
    assert not any(
        field.changed
        for impact in unchanged.libraries
        for case in impact.comparisons
        for field in case.fields
    )
    monkeypatch.setattr(policy_application, "evaluate_report", evaluate_report)
    reader.close()
    assert database.read_bytes() == before

    # Transport uses the same real SQLite/application capture, not canned rows.
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from tests.unit.review_app_fixture import (
        TEST_REACT_BUILD_IDENTITY,
        create_transport_test_app,
    )

    password = tmp_path / "owner.hash"
    password.write_text(PasswordHasher().hash("synthetic owner password"))
    password.chmod(0o600)
    repository = open_repository(database, cross_thread=True)
    configuration = ReviewWebConfiguration(
        canonical_origin="http://review.test",
        owner_password_hash_file=str(password),
    )
    app = create_transport_test_app(
        repository,
        configuration,
        policy_libraries=tuple((library, None) for library in libraries),
    )
    with TestClient(app, base_url="http://review.test") as client:
        headers = {
            "Origin": "http://review.test",
            "X-ML-Build-Identity": TEST_REACT_BUILD_IDENTITY,
        }
        login = client.post(
            "/api/v1/session/login",
            headers=headers,
            json={"password": "synthetic owner password"},
        )
        assert login.status_code == 200
        headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(
            "/api/v1/metadata-policy/preview",
            headers=headers,
            json={
                "name": draft.name,
                "options": dict(draft.options),
                "destinations": dict(draft.destinations),
            },
        )
        assert response.status_code == 200, response.text
        assert [item["comparisons"] for item in response.json()["libraries"]] == [
            json.loads(json.dumps([case.to_data() for case in impact.comparisons]))
            for impact in preview.libraries
        ]
    repository.close()


def test_capture_holds_one_snapshot_across_generation_and_case_changes(
    tmp_path, monkeypatch
):
    import threading

    database = tmp_path / "state.sqlite"
    owner = open_repository(database, cross_thread=True)
    report = _case_report(1)
    owner.publish(report, lambda: True)
    proposal = evaluate_report(report)
    owner.publish_proposal(proposal)
    owner.admit_review_case(proposal)
    successor = _case_report(2)
    owner.publish(successor, lambda: True)
    successor_proposal = evaluate_report(successor)
    owner.publish_proposal(successor_proposal)
    owner.admit_review_case(successor_proposal)
    reader = open_repository(database, authority=owner.authority)
    original = reader._metadata_policy.metadata_policy_preview_baseline
    begin_write = threading.Event()
    commit_attempted = threading.Event()
    committed = threading.Event()
    failures: list[BaseException] = []

    def write_successor():
        try:
            assert begin_write.wait(5), "capture never released writer"
            owner._connection.execute("BEGIN IMMEDIATE")
            owner._connection.execute(
                "DELETE FROM current_review_packages WHERE item_id='item-01'"
            )
            # Raw generation mutation isolates read-snapshot consistency;
            # activation transition behavior has its own public-seam proof.
            owner._connection.execute(
                "UPDATE metadata_policy_state SET generation=generation+1 WHERE singleton=1"
            )
            commit_attempted.set()
            owner._connection.commit()
            committed.set()
        except BaseException as error:
            owner._connection.rollback()
            failures.append(error)
            commit_attempted.set()

    def change_after_baseline(libraries):
        baseline = original(libraries)
        begin_write.set()
        assert commit_attempted.wait(5), "writer never attempted commit"
        assert not failures
        assert not committed.is_set()
        return baseline

    worker = threading.Thread(target=write_successor)
    try:
        monkeypatch.setattr(
            reader._metadata_policy,
            "metadata_policy_preview_baseline",
            change_after_baseline,
        )
        worker.start()
        capture = reader.capture_policy_preview(("ebooks",), 20)
        assert capture.generation == 1
        assert capture.libraries[0].current_unapplied_count == 2
        assert [report.target.item for report, _ in capture.libraries[0].pairs] == [
            "item-01",
            "item-02",
        ]
        assert not reader._connection.in_transaction
        worker.join(5)
        assert not worker.is_alive()
        assert not failures
        assert committed.is_set()
        monkeypatch.setattr(
            reader._metadata_policy, "metadata_policy_preview_baseline", original
        )
        next_capture = reader.capture_policy_preview(("ebooks",), 20)
        assert next_capture.generation == 2
        assert next_capture.libraries[0].current_unapplied_count == 1
        assert [
            report.target.item for report, _ in next_capture.libraries[0].pairs
        ] == ["item-02"]
    finally:
        begin_write.set()
        reader.close()
        worker.join(5)
        owner.close()


def test_immutable_preview_refuses_a_changed_database(tmp_path):
    database = tmp_path / "state.sqlite"
    owner = open_repository(database)
    reader = open_shadow_preview_repository(database)
    try:
        assert reader.capture_policy_preview(("ebooks",), 20).generation == 1
        owner.publish(_case_report(1), lambda: True)
        with pytest.raises(MetadataReportError, match="database authority changed"):
            reader.capture_policy_preview(("ebooks",), 20)
    finally:
        reader.close()
        owner.close()


@pytest.mark.parametrize(
    (
        "current",
        "active_option",
        "active_route",
        "draft_option",
        "draft_route",
        "changed",
    ),
    [
        (("Exploration",), "fill_empty", "ask", "fill_empty", "automatic", True),
        (("Exploration",), "fill_empty", "automatic", "fill_empty", "ask", True),
        (None, "fill_empty", "ask", "fill_empty", "ignore", True),
        (None, "off", "ask", "off", "ask", False),
        (None, "fill_empty", "automatic", "fill_empty", "automatic", False),
    ],
)
def test_actual_candidate_routing_is_distinct_from_library_value_change(
    tmp_path, current, active_option, active_route, draft_option, draft_route, changed
) -> None:
    from mechanical_librarian.application.metadata_policy import preview_policy
    from mechanical_librarian.domain.metadata_policy import (
        DEFAULT_OPTIONS,
        OPTION_CATALOGUE,
        MetadataPolicyProfile,
        resolve_policy,
    )
    from mechanical_librarian.domain.metadata_report import (
        TargetDestination,
        TargetField,
        TargetFieldState,
    )
    from tests.support.ebook_synopsis import synopsis_report

    source = synopsis_report()
    report = MetadataReport.create(
        target=TargetFacts(
            source.target.connection,
            source.target.library,
            source.target.item,
            tuple(
                TargetField(TargetDestination.TAGS, TargetFieldState.VALUE, current)
                if field.destination is TargetDestination.TAGS and current is not None
                else field
                for field in source.target.fields
            ),
        ),
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )
    destinations = {
        field: "unsupported" if field == "explicit" else "ask"
        for field in OPTION_CATALOGUE
    }
    active = MetadataPolicyProfile.create(
        "Active",
        dict(DEFAULT_OPTIONS) | {"tags": active_option},
        destinations | {"tags": active_route},
    )
    draft = MetadataPolicyProfile.create(
        "Renamed draft",
        dict(DEFAULT_OPTIONS) | {"tags": draft_option},
        destinations | {"tags": draft_route},
    )
    library = report.target.library
    resolved = resolve_policy(active, library)
    repository = open_repository(tmp_path / "state.sqlite")
    try:
        repository.create_metadata_policy_draft(active.canonical_json)
        repository.activate_metadata_policy(
            active.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((library, resolved.resolved_id, resolved.canonical_json),),
            1,
        )
        repository.publish(report, lambda: True)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
        preview = preview_policy(draft, (library,), {}, repository)
        tags = next(
            field
            for field in preview.libraries[0].comparisons[0].fields
            if field.draft.field == "tags"
        )
        assert tags.changed is changed
        assert (tags.active_destination, tags.draft_destination) == (
            active_route,
            draft_route,
        )
        if current is not None:
            assert tags.active.desired_value == tags.draft.desired_value == current
            assert (
                tags.active.source_candidate_id
                is tags.draft.source_candidate_id
                is None
            )
        assert not repository._connection.in_transaction
    finally:
        repository.close()


@pytest.mark.parametrize(
    "corruption", ("generation", "count", "library", "resolution", "pair")
)
def test_capture_rejects_malformed_or_incompatible_inputs(tmp_path, corruption) -> None:
    from dataclasses import replace

    from mechanical_librarian.application.metadata_policy import (
        MetadataPolicyApplicationError,
        preview_policy,
    )
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )

    repository = open_repository(tmp_path / "state.sqlite")
    try:
        report = _case_report(1)
        proposal = evaluate_report(report)
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
        captured = repository.capture_policy_preview(("ebooks",), 20)
        library = captured.libraries[0]
        if corruption == "generation":
            captured = replace(captured, generation=0)
        elif corruption == "count":
            captured = replace(
                captured, libraries=(replace(library, current_unapplied_count=0),)
            )
        elif corruption == "library":
            captured = replace(captured, libraries=(replace(library, library="other"),))
        elif corruption == "resolution":
            active = captured.active
            other = MetadataPolicyProfile.create(
                "Different active", dict(active.options), dict(active.destinations)
            )
            captured = replace(
                captured,
                libraries=(
                    replace(library, resolution=resolve_policy(other, "ebooks")),
                ),
            )
        else:
            captured = replace(
                captured,
                libraries=(replace(library, pairs=((_case_report(2), proposal),)),),
            )

        class CapturedRepository:
            def capture_policy_preview(self, libraries, limit):
                return captured

        with pytest.raises(MetadataPolicyApplicationError, match="unavailable"):
            preview_policy(captured.active, ("ebooks",), {}, CapturedRepository())
        assert not repository._connection.in_transaction
    finally:
        repository.close()
