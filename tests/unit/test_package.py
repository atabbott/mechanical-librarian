from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

import pytest

import mechanical_librarian
from mechanical_librarian.bootstrap import main


def test_installed_metadata_matches_exposed_version() -> None:
    assert metadata.version("mechanical-librarian") == mechanical_librarian.__version__


def test_distribution_excludes_source_only_workbench() -> None:
    files = tuple(str(path) for path in (metadata.files("mechanical-librarian") or ()))
    assert not any("mechanical_librarian_workbench" in path for path in files)


def test_composed_cli_exposes_help() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0


def test_all_modules_import_outside_checkout_without_creating_state() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import importlib, pkgutil, mechanical_librarian; "
                    "[importlib.import_module(item.name) for item in "
                    "pkgutil.walk_packages(mechanical_librarian.__path__, "
                    "mechanical_librarian.__name__ + '.')]; "
                    "print(mechanical_librarian.__version__)"
                ),
            ],
            cwd=temporary_directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        created_entries = list(directory.iterdir())

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == mechanical_librarian.__version__
    assert created_entries == []


def test_installed_reviewer_cost_resource_is_closed_and_unmeasured() -> None:
    from importlib.resources import files

    from mechanical_librarian.domain.reviewer_cost import ReviewerCostEvidence

    evidence = ReviewerCostEvidence.model_validate_json(
        files("mechanical_librarian")
        .joinpath("resources/reviewer-cost-evidence-v1.json")
        .read_bytes()
    )
    assert evidence.measured_run is None
    assert evidence.models == ()
