from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.build_frontend import (
    FrontendBuildError,
    _validate_bundle,
    build_frontend,
    toolchain_versions,
)

from tests.unit.test_react_assets import _write_bundle

ROOT = Path(__file__).parents[2]


def _manifest(root: Path) -> Path:
    path = root / "frontend/package.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "engines": {"node": "25.0.0", "npm": "12.0.0"},
                "packageManager": "npm@12.0.0",
            }
        )
    )
    return path


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        {},
        {"engines": {}},
        {
            "engines": {"node": "^25.0.0", "npm": "12.0.0"},
            "packageManager": "npm@12.0.0",
        },
        {
            "engines": {"node": "25.0.0", "npm": "12.0.0"},
            "packageManager": "npm@11.0.0",
        },
    ],
)
def test_manifest_refuses_missing_malformed_and_disagreeing_pins(
    tmp_path: Path, invalid: object
) -> None:
    path = _manifest(tmp_path)
    path.write_text(json.dumps(invalid))
    with pytest.raises(FrontendBuildError, match="manifest_invalid"):
        toolchain_versions(tmp_path)
    path.unlink()
    with pytest.raises(FrontendBuildError, match="manifest_invalid"):
        toolchain_versions(tmp_path)


@pytest.mark.parametrize(
    "versions,error",
    [
        (("v24.0.0",), "node_version_invalid"),
        (("v25.0.0", "11.0.0"), "npm_version_invalid"),
    ],
)
def test_real_executable_versions_must_match_manifest(
    tmp_path: Path, versions: tuple[str, ...], error: str
) -> None:
    _manifest(tmp_path)
    assert toolchain_versions(tmp_path) == ("25.0.0", "12.0.0")
    with (
        patch("scripts.build_frontend._version", side_effect=versions),
        pytest.raises(FrontendBuildError, match=error),
    ):
        build_frontend(tmp_path, tmp_path / "out")


def test_prebuilt_bundle_needs_no_local_node_or_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prebuilt = tmp_path / "prebuilt"
    prebuilt.mkdir()
    (prebuilt / "asset").write_text("qualified")
    monkeypatch.setenv("ML_FRONTEND_PREBUILT_DIR", str(prebuilt))
    with (
        patch("scripts.build_frontend._validate_bundle") as validate,
        patch(
            "scripts.build_frontend._version",
            side_effect=AssertionError("unexpected executable"),
        ),
    ):
        build_frontend(tmp_path, tmp_path / "out")
    validate.assert_called_once_with(tmp_path, prebuilt)
    assert (tmp_path / "out/asset").read_text() == "qualified"


@pytest.mark.parametrize("tool", ["node", "npm"])
def test_actual_version_subprocess_refuses_a_mismatched_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    _manifest(tmp_path)
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    for name, version in (("node", "v25.0.0"), ("npm", "12.0.0")):
        executable = bin_path / name
        executable.write_text(
            "#!/bin/sh\nprintf '%s\\n' '"
            + ("0.0.0" if name == tool else version)
            + "'\n"
        )
        executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_path) + os.pathsep + os.environ["PATH"])
    with pytest.raises(FrontendBuildError, match=f"frontend_{tool}_version_invalid"):
        build_frontend(tmp_path, tmp_path / "out")


def test_build_refuses_a_bundle_with_an_unverifiable_sibling(tmp_path: Path) -> None:
    _write_bundle(tmp_path, encoded=True)
    _validate_bundle(ROOT, tmp_path)
    # The server would fall back to identity; the build must not ship it.
    (tmp_path / "assets/index-deadbeef.js.gz").write_bytes(b"corrupt")

    with pytest.raises(FrontendBuildError, match="frontend_bundle_invalid"):
        _validate_bundle(ROOT, tmp_path)
