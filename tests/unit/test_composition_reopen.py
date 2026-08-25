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

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import pipeline_scan_id
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
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
    assert composition._store_first_preparation("store", ("setup_complete",), False)
    assert composition._store_first_preparation("store", (), True)
    assert not composition._store_first_preparation("store", (), False)
    assert not composition._store_first_preparation("file", ("setup_complete",), True)


def test_first_preparation_marker_survives_settings_edit_and_repository_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = composition.open_repository(database)
    assert not repository.has_first_boot_preparation()
    settings = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v1",
            "revision": 1,
            "connections": [{"name": "primary", "endpoint": "https://library.invalid"}],
            "providers": [],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "books",
                    "media_kind": "ebook",
                    "provider_connections": [],
                    "mount": "books",
                }
            ],
        }
    )
    repository._connection.execute(  # noqa: SLF001 - composition persistence proof
        "INSERT INTO owner_settings VALUES(1,1,?)",
        (_canonical_json(settings.model_dump()),),
    )
    repository._connection.executemany(  # noqa: SLF001 - composition persistence proof
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        (
            ("connection.primary.key", "target-secret-1234"),
            ("owner.password.verifier", "$argon2id$synthetic"),
        ),
    )
    requested = repository.request_review_scan(
        "primary", "books", "books", "b" * 64, 1, first_boot=True
    )
    assert requested.scan_id == pipeline_scan_id("primary", "books", first_boot=True)
    repository.owner_settings_store().write(
        settings.model_copy(update={"revision": 2, "reduced_motion": "always_reduced"}),
        1,
        (),
    )
    repository.close()

    reopened = composition.open_repository(database, create=False)
    try:
        assert reopened.has_first_boot_preparation()
        assert reopened.owner_settings_store().read().document.revision == 2
        reopened._connection.execute(  # noqa: SLF001 - lifecycle persistence proof
            "UPDATE review_preparation_scans SET state='completed',total=1,"
            "updated_at=2 WHERE scan_id=?",
            (requested.scan_id,),
        )
        assert reopened.has_first_boot_preparation()
        rescan = reopened.request_review_scan(
            "primary", "books", "books", "b" * 64, 3, first_boot=True
        )
        assert rescan.scan_id == pipeline_scan_id("primary", "books")
        assert not reopened.has_first_boot_preparation()
    finally:
        reopened.close()


def test_normal_store_scan_history_never_becomes_first_preparation(
    tmp_path: Path,
) -> None:
    repository = composition.open_repository(tmp_path / "application.sqlite3")
    try:
        requested = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 1
        )
        assert requested.scan_id == pipeline_scan_id("primary", "books")
        assert not repository.has_first_boot_preparation()
    finally:
        repository.close()


def test_completed_first_library_keeps_marker_until_initial_cohort_is_terminal(
    tmp_path: Path,
) -> None:
    repository = composition.open_repository(tmp_path / "application.sqlite3")
    try:
        first = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 1, first_boot=True
        )
        second = repository.request_review_scan(
            "primary", "history", "history", "b" * 64, 1, first_boot=True
        )
        repository._connection.execute(  # noqa: SLF001 - lifecycle persistence proof
            "UPDATE review_preparation_scans SET state='completed',total=1,"
            "updated_at=2 WHERE scan_id=?",
            (first.scan_id,),
        )

        held = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 3, first_boot=True
        )
        assert held.scan_id == first.scan_id
        assert held.state == "completed"
        assert repository.has_first_boot_preparation()

        repository._connection.execute(  # noqa: SLF001 - lifecycle persistence proof
            "UPDATE review_preparation_scans SET state='completed',total=1,"
            "updated_at=4 WHERE scan_id=?",
            (second.scan_id,),
        )
        ordinary = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 5, first_boot=True
        )
        assert ordinary.scan_id == pipeline_scan_id("primary", "books")
        assert not repository.has_first_boot_preparation()
    finally:
        repository.close()
