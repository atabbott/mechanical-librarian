from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from mechanical_librarian.infrastructure.configuration import ConfigurationInput


def _qualifier() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "qualify_container_lifecycle.py"
    specification = importlib.util.spec_from_file_location(
        "qualify_container_lifecycle", path
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_generated_container_configuration_admits_the_current_schema() -> None:
    configuration = _qualifier()._configuration()

    admitted = ConfigurationInput.model_validate(configuration)

    assert admitted.schema_version == "config-v8"


def test_first_run_partial_volume_creation_enters_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qualifier = _qualifier()
    created: set[str] = set()
    removed: list[str] = []

    def fail_second_create(arguments: list[str], *, capture: bool = True) -> str:
        del capture
        if arguments[:3] != ["docker", "volume", "create"]:
            raise AssertionError(arguments)
        name = arguments[-1]
        created.add(name)
        if len(created) == 2:
            raise qualifier.QualificationError("synthetic_partial_create")
        return name

    def owned(kind: str, name: str, run_id: str) -> bool:
        return kind == "volume" and name in created and run_id == "synthetic-run"

    def remove(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:3] == ["docker", "volume", "rm"]:
            removed.append(arguments[-1])
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(qualifier, "_run", fail_second_create)
    monkeypatch.setattr(qualifier, "_owned", owned)
    monkeypatch.setattr(qualifier.subprocess, "run", remove)

    with pytest.raises(qualifier.QualificationError, match="synthetic_partial_create"):
        qualifier._qualify_first_run("synthetic-image", "synthetic-run", "suffix")

    assert set(removed) == created
    assert len(removed) == len(created)
