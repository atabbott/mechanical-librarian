from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.bootstrap.composition import (
    open_field_effect_activity_projection,
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


def test_independent_activity_reader_gets_busy_configuration_and_live_readiness() -> (
    None
):
    repository = Mock()
    projection = Mock()
    configuration = Mock(review=object())
    capability_readiness = Mock()

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
            configuration,
            Path("/database.sqlite3"),
            object(),
            None,
            capability_readiness,
        )

    assert actual is projection
    repository.configure_activity_page_reader.assert_called_once_with()
    compose.assert_called_once_with(
        configuration, repository, None, capability_readiness
    )
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
            review=SimpleNamespace(graceful_shutdown_seconds=5),
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

        assert "continuous-field-activity-v2" == activity["schema_version"]
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


def test_show_and_select_delegate_closed_values() -> None:
    runner = Mock(return_value={"schema_version": "cover-consideration-v1"})
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
    assert json.loads(output.getvalue())["schema_version"] == "cover-consideration-v1"
    runner.assert_called_once_with(
        Path("/config.json"),
        Path("/database.sqlite3"),
        Path("/artifacts"),
        "review_case_" + "a" * 64,
        "show",
        None,
        None,
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
            },
        ]
    }


def _run(repository: Mock, application: Mock, *arguments: object) -> object:
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
            "mechanical_librarian.bootstrap.composition.compose_review_application",
            return_value=application,
        ),
    ):
        return run_cover_review(
            Path("/config.json"),
            Path("/database.sqlite3"),
            Path("/artifacts"),
            _CASE,
            *arguments,
        )


@pytest.mark.parametrize(
    ("action", "projection"),
    (("history", "history"), ("show", "prepare_cover_consideration")),
)
def test_a_projecting_action_reads_without_deciding(
    action: str, projection: str
) -> None:
    repository, application = _composed({})
    getattr(application, projection).return_value = {"schema_version": "projection"}

    assert {"schema_version": "projection"} == _run(
        repository, application, action, None, None
    )

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
