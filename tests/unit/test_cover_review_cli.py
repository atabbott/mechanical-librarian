from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    ReviewWebConfiguration,
)
from mechanical_librarian.bootstrap.composition import (
    open_field_effect_activity_projection,
    open_field_effect_activity_readers,
    run_cover_review,
    run_provider_recovery_once,
    run_review_activity,
)
from mechanical_librarian.interfaces.cli import main


def test_activity_composition_calls_the_shared_query_only_projection() -> None:
    repository = Mock()
    projection = Mock()
    expected = {
        "events": [{"state": "verified"}],
        "schema_version": "continuous-field-activity-v1",
    }
    projection.activity.return_value = expected
    configuration = Mock(review=object())

    with (
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=configuration,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            return_value=repository,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.compose_field_effect_activity_projection",
            return_value=projection,
        ) as compose,
    ):
        actual = run_review_activity(
            Path("/config.json"),
            Path("/database.sqlite3"),
        )

    assert expected == actual
    compose.assert_called_once_with(configuration, repository)
    projection.activity.assert_called_once_with()
    repository.close.assert_called_once_with()


def test_independent_activity_reader_gets_the_bounded_busy_configuration() -> None:
    repository = Mock()
    projection = Mock()
    configuration = Mock(review=object())

    with (
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            return_value=repository,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.compose_field_effect_activity_projection",
            return_value=projection,
        ) as compose,
    ):
        actual, close = open_field_effect_activity_projection(
            configuration, Path("/database.sqlite3"), object()
        )

    assert actual is projection
    repository.configure_activity_page_reader.assert_called_once_with(
        None, readiness_window=None, documents=None
    )
    compose.assert_called_once_with(configuration, repository, None)
    close()
    repository.close.assert_called_once_with()


def test_activity_composition_does_not_change_installed_state() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "application.sqlite3"
        repository = open_repository(database)
        repository.close()
        configuration = SimpleNamespace(
            libraries=(SimpleNamespace(connection="abs", name="books"),),
            review=ReviewWebConfiguration(
                canonical_origin="http://127.0.0.1:8080",
                owner_password_hash_file=str(root / "owner.hash"),
                graceful_shutdown_seconds=5,
            ),
            select=Mock(side_effect=AssertionError("target configuration used")),
        )
        before_bytes = database.read_bytes()
        before_entries = {path.name for path in root.iterdir()}

        with (
            patch(
                "mechanical_librarian.bootstrap.composition.load_configuration_file",
                return_value=configuration,
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.compose_audiobookshelf_field_effect_capabilities",
                side_effect=AssertionError("capability adapter composed"),
            ) as capabilities,
            patch(
                "mechanical_librarian.bootstrap.composition.target_client",
                side_effect=AssertionError("target client composed"),
            ) as target_client,
        ):
            activity = run_review_activity(
                root / "config.json",
                database,
            )

        assert "continuous-field-activity-v3" == activity["schema_version"]
        assert before_bytes == database.read_bytes()
        assert before_entries == {path.name for path in root.iterdir()}
        assert not (root / "private").exists()
        assert not (root / "cover-artifacts").exists()
        configuration.select.assert_not_called()
        capabilities.assert_not_called()
        target_client.assert_not_called()


def test_activity_exposes_guarded_and_recovery_states_from_shared_projection() -> None:
    states = ("planned", "verified", "stale", "recovery_required")
    runner = Mock(
        return_value={
            "events": [
                {"plan_id": f"field_effect_{index}", "state": state}
                for index, state in enumerate(states)
            ],
            "schema_version": "continuous-field-activity-v1",
        }
    )
    output = io.StringIO()

    assert (
        main(
            [
                "review-activity",
                "--config",
                "/config.json",
                "--database",
                "/database.sqlite3",
            ],
            review_activity_runner=runner,
            stdout=output,
        )
        == 0
    )

    runner.assert_called_once_with(
        Path("/config.json"),
        Path("/database.sqlite3"),
    )
    assert (
        tuple(item["state"] for item in json.loads(output.getvalue())["events"])
        == states
    )


def test_activity_rejects_obsolete_unbound_paths() -> None:
    runner = Mock()
    errors = io.StringIO()

    assert (
        main(
            [
                "review-activity",
                "--config",
                "/config.json",
                "--database",
                "/database.sqlite3",
                "--catalogue",
                "/catalogue.sqlite3",
            ],
            review_activity_runner=runner,
            stderr=errors,
        )
        == 2
    )

    runner.assert_not_called()
    assert errors.getvalue() == "Review server failed: invalid invocation.\n"


def test_provider_recovery_refuses_absent_review_configuration() -> None:
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=SimpleNamespace(review=None),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.SQLiteProviderRecoveryStore.open",
            side_effect=AssertionError("recovery store opened"),
        ) as store,
        patch(
            "mechanical_librarian.bootstrap.composition.create_review_pipeline",
            side_effect=AssertionError("review pipeline composed"),
        ) as pipeline,
        pytest.raises(ConfigurationError, match="review configuration is unavailable"),
    ):
        run_provider_recovery_once(
            Path("/config.json"),
            Path("/database.sqlite3"),
            Path("/catalogue.sqlite3"),
            Path("/artifacts"),
        )

    store.assert_not_called()
    pipeline.assert_not_called()


def test_provider_recovery_uses_one_explicit_owner_reviewer_authority() -> None:
    review = object()
    recovery = Mock()
    repository = Mock()
    reviewer_access = Mock()
    supervisor = Mock()
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=SimpleNamespace(review=review),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.SQLiteProviderRecoveryStore.open",
            return_value=recovery,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition._private_root",
            return_value=Path("/private"),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            return_value=repository,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition._compose_reviewer_access",
            return_value=reviewer_access,
        ) as compose_access,
        patch(
            "mechanical_librarian.bootstrap.composition.create_review_pipeline",
            return_value=supervisor,
        ) as compose_pipeline,
    ):
        run_provider_recovery_once(
            Path("/config.json"),
            Path("/database.sqlite3"),
            Path("/catalogue.sqlite3"),
            Path("/artifacts"),
        )

    compose_access.assert_called_once_with(repository, review)
    assert compose_pipeline.call_args.args[4] is reviewer_access
    supervisor.recover_provider_once.assert_called_once_with()
    supervisor.stop.assert_called_once_with()
    reviewer_access.close.assert_called_once_with()
    repository.close.assert_called_once_with()


def test_show_and_select_delegate_closed_values() -> None:
    runner = Mock(return_value={"schema_version": "cover-consideration-v4"})
    output = io.StringIO()
    assert (
        main(
            [
                "cover-review",
                "show",
                "--config",
                "/config.json",
                "--database",
                "/database.sqlite3",
                "--artifacts",
                "/artifacts",
                "--case",
                "review_case_" + "a" * 64,
            ],
            cover_review_runner=runner,
            stdout=output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["schema_version"] == "cover-consideration-v4"
    runner.assert_called_once_with(
        Path("/config.json"),
        Path("/database.sqlite3"),
        Path("/artifacts"),
        "review_case_" + "a" * 64,
        "show",
        None,
        None,
        None,
        False,
    )

    runner.reset_mock()
    assert (
        main(
            [
                "cover-review",
                "history",
                "--config",
                "/config.json",
                "--database",
                "/database.sqlite3",
                "--artifacts",
                "/artifacts",
                "--case",
                "review_case_" + "a" * 64,
            ],
            cover_review_runner=runner,
            stdout=io.StringIO(),
        )
        == 0
    )
    runner.assert_called_once_with(
        Path("/config.json"),
        Path("/database.sqlite3"),
        Path("/artifacts"),
        "review_case_" + "a" * 64,
        "history",
        None,
        None,
        None,
        False,
    )

    runner.reset_mock()
    assert (
        main(
            [
                "cover-review",
                "select",
                "--config",
                "/config.json",
                "--database",
                "/database.sqlite3",
                "--artifacts",
                "/artifacts",
                "--case",
                "review_case_" + "a" * 64,
                "--artifact",
                "cover_" + "b" * 64,
                "--idempotency-key",
                "owner-command-0001",
            ],
            cover_review_runner=runner,
            stdout=io.StringIO(),
        )
        == 0
    )
    runner.assert_called_once_with(
        Path("/config.json"),
        Path("/database.sqlite3"),
        Path("/artifacts"),
        "review_case_" + "a" * 64,
        "select",
        "cover_" + "b" * 64,
        "owner-command-0001",
        None,
        False,
    )


def test_invalid_choice_shape_fails_before_runner() -> None:
    runner = Mock()
    errors = io.StringIO()
    assert (
        main(
            [
                "cover-review",
                "select",
                "--config",
                "/config.json",
                "--database",
                "/database.sqlite3",
                "--artifacts",
                "/artifacts",
                "--case",
                "review_case_" + "a" * 64,
                "--idempotency-key",
                "owner-command-0001",
            ],
            cover_review_runner=runner,
            stderr=errors,
        )
        == 1
    )
    runner.assert_not_called()
    assert errors.getvalue() == "Review server failed safely.\n"


# The `cover-review` runner every test above substitutes with a `Mock`. Nothing
# else runs it: it is a CLI subcommand, so neither the container lifecycle
# qualifier nor the scale qualification reaches it, and the mutation audit
# recorded all 150 of its mutants as reached by no test (#773). These four
# cases pin what the body decides rather than what the parser hands it.
_CASE = "review_case_" + "c" * 64


def _composed(detail: object) -> tuple[Mock, Mock]:
    repository = Mock()
    application = Mock()
    application.case_detail.return_value = SimpleNamespace(to_data=lambda: detail)
    return repository, application


def _cover_detail() -> dict[str, object]:
    return {
        "fields": [
            {"destination": "title", "cover_consideration": None},
            {
                "destination": "cover",
                "proposal_id": "proposal_" + "d" * 64,
                "target_fingerprint": "fingerprint_" + "e" * 64,
                "cover_consideration": {"consideration_id": "consideration_1"},
                "current_cover_selection_id": None,
            },
        ]
    }


def _run(repository: Mock, application: Mock, *arguments: object) -> object:
    reviewer_access = Mock()
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=SimpleNamespace(review=object()),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            return_value=repository,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition._compose_cover_considerations",
            return_value=object(),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition._compose_reviewer_access",
            return_value=reviewer_access,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.compose_review_application",
            return_value=application,
        ),
    ):
        result = run_cover_review(
            Path("/config.json"),
            Path("/database.sqlite3"),
            Path("/artifacts"),
            _CASE,
            *arguments,
        )
    reviewer_access.close.assert_called_once_with()
    return result


@pytest.mark.parametrize(
    ("action", "projection"),
    (("history", "history"), ("show", "prepare_cover_consideration")),
)
def test_a_projecting_action_reads_without_deciding(
    action: str, projection: str
) -> None:
    repository, application = _composed(_cover_detail())
    getattr(application, projection).return_value = {"schema_version": "projection"}

    expected = (
        {"schema_version": "projection"}
        if action == "history"
        else {"consideration_id": "consideration_1", "current_cover_selection_id": None}
    )
    assert expected == _run(repository, application, action, None, None)

    getattr(application, projection).assert_called_once_with(_CASE)
    application.record_decision.assert_not_called()
    repository.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("action", "outcome", "selected"),
    (
        ("keep", "keep_current", None),
        ("no-action", "reject", None),
        ("select", "accept", {"kind": "cover", "value": "cover_" + "f" * 64}),
    ),
)
def test_a_deciding_action_records_its_own_outcome_against_the_cover_field(
    action: str, outcome: str, selected: dict[str, object] | None
) -> None:
    repository, application = _composed(_cover_detail())
    application.record_decision.return_value = {"status": "recorded"}

    result = _run(
        repository,
        application,
        action,
        None if selected is None else selected["value"],
        "owner-command-0001",
    )

    assert {
        "consideration_id": "consideration_1",
        "decision": {"status": "recorded"},
        "schema_version": "cover-review-cli-v1",
    } == result
    application.record_decision.assert_called_once_with(
        _CASE,
        proposal_id="proposal_" + "d" * 64,
        target_fingerprint="fingerprint_" + "e" * 64,
        destination="cover",
        actor="owner",
        outcome=outcome,
        selected_value=selected,
        idempotency_key="owner-command-0001",
        consideration_id="consideration_1",
        cover_confirmation={
            "expected_selection_id": None,
            "confirm_unverified_match": False,
        },
    )
    repository.close.assert_called_once_with()


def test_a_decision_without_an_idempotency_key_records_nothing() -> None:
    repository, application = _composed(_cover_detail())

    with pytest.raises(ValueError, match="idempotency key is required"):
        _run(repository, application, "keep", None, None)

    application.record_decision.assert_not_called()
    repository.close.assert_called_once_with()


def test_cover_review_refuses_an_absent_review_configuration() -> None:
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=SimpleNamespace(review=None),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            side_effect=AssertionError("database opened"),
        ) as repository,
        pytest.raises(ConfigurationError, match="review configuration is unavailable"),
    ):
        run_cover_review(
            Path("/config.json"),
            Path("/database.sqlite3"),
            Path("/artifacts"),
            _CASE,
            "show",
            None,
            None,
        )

    repository.assert_not_called()


@pytest.mark.parametrize("unknown", [False, True])
def test_installed_cover_commands_persist_explicit_confirmation_and_revision(
    tmp_path, unknown
):
    from dataclasses import replace

    from mechanical_librarian.domain.metadata_report import MetadataReport
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.cover_producers import _qualified_cover_report
    from tests.unit.test_metadata_configuration import _review, _source

    tmp_path.chmod(0o700)
    report, _ = _qualified_cover_report(tmp_path)
    if unknown:
        report = MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
            cover_sources=report.cover_sources,
        )
    database = tmp_path / "application.sqlite"
    repository = open_repository(database)
    repository.publish(report, lambda: True)
    proposals = evaluate_report(report)
    repository.publish_proposal(proposals)
    case_id = json.loads(repository.admit_review_case(proposals))["review_case_id"]
    repository.close()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({**_source(tmp_path), "review": _review(tmp_path)}))

    def command(action, *extra, success=True):
        output, errors = io.StringIO(), io.StringIO()
        status = main(
            [
                "cover-review",
                action,
                "--config",
                str(config),
                "--database",
                str(database),
                "--artifacts",
                str(tmp_path / "artifacts"),
                "--case",
                case_id,
                *extra,
            ],
            cover_review_runner=run_cover_review,
            stdout=output,
            stderr=errors,
        )
        assert status == (0 if success else 1), errors.getvalue()
        return json.loads(output.getvalue()) if success else None

    shown = command("show")
    assert shown["current_cover_selection_id"] is None
    chosen = next(c for c in shown["candidates"] if not c["incumbent"])
    request = [
        "--artifact",
        chosen["artifact_id"],
        "--idempotency-key",
        "cli-select-command-0001",
    ]
    if unknown:
        command("select", *request, success=False)
        request += ["--confirm-unverified-match"]
    from mechanical_librarian.adapters.persistence.provider_recovery import (
        SQLiteProviderRecoveryStore,
    )
    from tests.unit.test_provider_recovery import _job

    recovery_path = tmp_path / "private" / "provider-recovery.sqlite"
    recovery_path.parent.mkdir(mode=0o700)
    recovery = SQLiteProviderRecoveryStore.open(recovery_path)
    recovery.enqueue_jobs(
        (
            replace(
                _job("target"),
                target_connection=report.target.connection,
                library=report.target.library,
                item=report.target.item,
                source_report_id=report.report_id,
                package_report_id=report.report_id,
            ),
        ),
        enqueued_at=3,
    )
    assert (
        recovery.claim_one(generation=1, now=7203, token="cli-active-fetch") is not None
    )
    recovery.close()
    before = recovery_path.read_bytes()
    assert command("show")["provider_fetching"] is True
    command("select", *request, success=False)
    assert recovery_path.read_bytes() == before
    recovery_path.unlink()  # End this isolated acquisition fixture, not a CLI operation.
    assert command("show")["provider_fetching"] is False
    accepted = command("select", *request)
    assert command("select", *request) == accepted
    first = command("show")["current_cover_selection_id"]
    assert first is not None
    command("keep", "--idempotency-key", "cli-keep-command-0001", success=False)
    command(
        "keep",
        "--idempotency-key",
        "cli-keep-command-0001",
        "--expected-selection-id",
        first,
    )
    second = command("show")["current_cover_selection_id"]
    assert second != first
    command("select", *request, success=False)
    command(
        "no-action",
        "--idempotency-key",
        "cli-reject-command-0001",
        "--expected-selection-id",
        second,
    )
    assert not (tmp_path / "private" / "provider-recovery.sqlite").exists()
    reopened = open_repository(database, create=False)
    try:
        assert reopened._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (3,)
        assert reopened._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (3,)
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "extra",
    [
        ["--expected-selection-id", "not-a-selection"],
        ["--confirm-unverified-match"],
    ],
)
def test_invalid_cover_confirmation_never_invokes_runner(extra):
    runner = Mock()
    assert (
        main(
            [
                "cover-review",
                "keep",
                "--config",
                "/config.json",
                "--database",
                "/database.sqlite",
                "--artifacts",
                "/artifacts",
                "--case",
                _CASE,
                "--idempotency-key",
                "command-invalid-0001",
                *extra,
            ],
            cover_review_runner=runner,
            stderr=io.StringIO(),
        )
        == 1
    )
    runner.assert_not_called()


def test_read_only_recovery_open_never_initializes_absent_or_empty_state(tmp_path):
    import sqlite3

    from mechanical_librarian.adapters.persistence.provider_recovery import (
        SQLiteProviderRecoveryStore,
    )

    path = tmp_path / "recovery.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteProviderRecoveryStore.open(path, read_only=True)
    assert not path.exists()
    path.touch()
    from mechanical_librarian.adapters.persistence.provider_recovery import (
        ProviderRecoveryStoreError,
    )

    with pytest.raises(ProviderRecoveryStoreError, match="unavailable|incompatible"):
        SQLiteProviderRecoveryStore.open(path, read_only=True)
    assert path.read_bytes() == b""


def test_activity_reader_is_query_only_and_owns_its_admission(tmp_path: Path) -> None:
    import sqlite3

    from mechanical_librarian.application.configuration import (
        ApplicationConfiguration,
        LibraryConfiguration,
        TargetConnectionConfiguration,
    )
    from mechanical_librarian.application.operation_admission import OperationAdmission

    root_admission = OperationAdmission()
    root = open_repository(
        tmp_path / "application.sqlite3",
        cross_thread=True,
        admission=root_admission,
    )
    projection, close = open_field_effect_activity_projection(
        ApplicationConfiguration(
            (TargetConnectionConfiguration("primary", "http://127.0.0.1:1", None),),
            (LibraryConfiguration("books", "primary", "Books", "ebook", None, ()),),
            (),
            (),
            review=ReviewWebConfiguration(
                "http://127.0.0.1:1", "/synthetic/owner.hash"
            ),
        ),
        tmp_path / "application.sqlite3",
        root.authority,
    )
    reader = projection._repository
    try:
        assert reader.operation_admission is not root_admission
        assert reader._connection.execute("PRAGMA query_only").fetchone() == (1,)
        assert root._connection.execute("PRAGMA query_only").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader._connection.execute("CREATE TABLE forbidden(value TEXT)")
        assert projection.activity()["events"] == []
    finally:
        close()
    with root_admission.enter():
        assert root._connection.execute("SELECT 1").fetchone() == (1,)
    root.close()


@pytest.mark.parametrize("stage", ("open", "configure"))
def test_activity_reader_construction_failure_releases_created_owners(
    stage: str,
) -> None:
    repository = Mock()
    failure = RuntimeError("reader unavailable")
    if stage == "configure":
        repository.configure_activity_page_reader.side_effect = failure
    with patch(
        "mechanical_librarian.bootstrap.composition.open_repository",
        side_effect=failure if stage == "open" else None,
        return_value=repository,
    ) as opened:
        with pytest.raises(RuntimeError) as raised:
            open_field_effect_activity_projection(
                Mock(), Path("/database.sqlite3"), object()
            )
    assert raised.value is failure
    assert opened.call_args.kwargs["admission"]._closed
    assert repository.close.call_count == (0 if stage == "open" else 1)


def test_permission_reader_partial_construction_closes_history() -> None:
    close = Mock()
    failure = RuntimeError("permission reader unavailable")
    with patch(
        "mechanical_librarian.bootstrap.composition.open_field_effect_activity_projection",
        side_effect=[(Mock(), close), failure],
    ):
        with pytest.raises(RuntimeError) as raised:
            open_field_effect_activity_readers(
                Mock(), Path("/database.sqlite3"), object()
            )
    assert raised.value is failure
    close.assert_called_once_with()
