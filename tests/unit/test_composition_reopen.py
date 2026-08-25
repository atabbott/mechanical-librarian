"""The composition seams the source-only workbench binds, and nothing else.

`reopen_*`, `create_proposal_set` and `run_metadata_report` are not CLI
subcommands; `bootstrap.composition.main` injects five runners and none of them
is here. Their only caller is `WorkbenchOperations.production_operations`, and
`tests/unit/test_runtime_workbench.py` proves the binding by identity while
substituting a fake for every operation, so no test in this suite executed a
line of them (#773). These are the two properties worth proving at that
altitude: a read never creates the database it was pointed at, and a seam whose
whole body is `load_configuration_file` plus a delegation delegates what it
loaded.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.infrastructure.first_boot import (
    FirstBootMode,
    select_first_boot_mode,
)
from mechanical_librarian.infrastructure.settings_authority import ReviewBootstrap


@pytest.mark.parametrize(
    ("operation", "arguments"),
    (
        ("reopen_metadata_report", ("report_id",)),
        (
            "reopen_current_metadata_report",
            ("primary", "ebooks", "item-1", "c" * 64),
        ),
        ("reopen_proposal_set", ("proposal_set_id",)),
        ("reopen_review_case", ("review_case_id",)),
    ),
)
def test_a_read_seam_refuses_an_absent_database_rather_than_creating_one(
    tmp_path: Path, operation: str, arguments: tuple[str, ...]
) -> None:
    database = tmp_path / "application.sqlite3"

    with pytest.raises(MetadataReportError):
        getattr(composition, operation)(database, *arguments)

    assert not database.exists()
    assert [] == sorted(tmp_path.iterdir())


def test_reopening_a_preview_refuses_an_absent_database_before_the_store(
    tmp_path: Path,
) -> None:
    """The artifact store is never reached, so it is never created either."""

    database = tmp_path / "application.sqlite3"
    artifacts = tmp_path / "artifacts"

    with (
        patch.object(
            composition,
            "CoverArtifactStore",
            side_effect=AssertionError("artifact store opened"),
        ) as store,
        pytest.raises(MetadataReportError),
    ):
        composition.reopen_cover_preview(
            database, artifacts, "report_id", "artifact_id", "thumbnail"
        )

    store.assert_not_called()
    assert not database.exists()
    assert not artifacts.exists()


@pytest.mark.parametrize(
    ("operation", "delegate", "arguments"),
    (
        (
            "create_proposal_set",
            "create_proposal_set_loaded",
            ("report_id",),
        ),
        (
            "run_metadata_report",
            "run_metadata_report_loaded",
            ("primary", "ebooks", "item-1", 0, "d" * 64),
        ),
    ),
)
def test_a_configuration_loading_seam_delegates_what_it_loaded(
    operation: str, delegate: str, arguments: tuple[object, ...]
) -> None:
    configuration = object()
    paths = (Path("/database"), Path("/artifacts"))

    with (
        patch.object(
            composition, "load_configuration_file", return_value=configuration
        ) as load,
        patch.object(composition, delegate, return_value="delegated") as loaded,
    ):
        result = getattr(composition, operation)(
            Path("/config.json"), *paths, *arguments
        )

    assert "delegated" == result
    load.assert_called_once_with(Path("/config.json"))
    assert loaded.call_args.args[0] is configuration
    assert paths == loaded.call_args.args[1:3]
    assert arguments == loaded.call_args.args[3:]


def test_setup_server_releases_claimed_state_for_restart_and_resume(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = composition.open_repository(database)
    bootstrap_verifier = PasswordHasher().hash("owner password")
    bootstrap = ReviewBootstrap(
        schema_version="review-bootstrap-v1",
        setup_owner_verifier=bootstrap_verifier,
        tls_certificate_file="certificate",
        tls_private_key_file="private-key",
        canonical_origin="https://setup.invalid",
    )

    def claim_before_shutdown(_server: object) -> None:
        state = repository.setup_state_store()
        document = state.read(bootstrap_verifier)
        state.write(
            document.model_copy(
                update={
                    "revision": document.revision + 1,
                    "completed_steps": ["claim"],
                    "claimed": True,
                }
            ),
            document.revision,
            bootstrap_verifier,
        )

    with (
        patch.object(
            composition,
            "open_review_tls_identity",
            return_value=nullcontext(
                SimpleNamespace(
                    certificate_file="certificate", private_key_file="private-key"
                )
            ),
        ),
        patch.object(composition, "open_repository", return_value=repository),
        patch.object(repository, "close", wraps=repository.close) as close,
        patch.object(
            composition.uvicorn.Server,
            "run",
            autospec=True,
            side_effect=claim_before_shutdown,
        ) as run,
    ):
        try:
            composition.run_setup_server(
                bootstrap,
                database,
                "127.0.0.1",
                18444,
                initialize=True,
            )
            close.assert_called_once_with()
        finally:
            if not close.called:
                repository.close()

    run.assert_called_once()
    assert not tuple(tmp_path.glob(".metadata-report-binding-*.sqlite"))

    def resumable_setup() -> bool:
        reopened = composition.open_repository(database, create=False)
        try:
            reopened.setup_state_store().read(bootstrap_verifier)
            return True
        finally:
            reopened.close()

    assert (
        select_first_boot_mode(
            tmp_path,
            complete_store=lambda: False,
            resumable_setup=resumable_setup,
        )
        is FirstBootMode.RESUME_SETUP
    )


def test_first_preparation_presentation_survives_restart_debt_acknowledgement() -> None:
    assert composition._store_first_preparation("store", 1, ("setup_complete",), False)
    assert composition._store_first_preparation("store", 2, (), True)
    assert not composition._store_first_preparation("store", 2, (), False)
    assert not composition._store_first_preparation("store", 3, (), True)
    assert not composition._store_first_preparation(
        "file", 1, ("setup_complete",), True
    )


def test_preparation_history_marker_survives_repository_reopen(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    repository = composition.open_repository(database)
    assert not repository.has_review_preparation_history()
    repository._connection.execute(  # noqa: SLF001 - persistence seam proof
        "INSERT INTO review_preparation_scans("
        "scan_id,connection_name,library_name,library_id,producer_fingerprint,"
        "state,requested_at,updated_at) VALUES(?,?,?,?,?,'queued',1,1)",
        ("review_scan_" + "a" * 64, "primary", "books", "books", "b" * 64),
    )
    repository.close()

    reopened = composition.open_repository(database, create=False)
    try:
        assert reopened.has_review_preparation_history()
    finally:
        reopened.close()
