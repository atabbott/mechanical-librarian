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

from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.bootstrap import composition


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
