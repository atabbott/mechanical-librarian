#!/usr/bin/env python3
"""Build one exact, out-of-tree React asset bundle for wheel/container use."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NODE_VERSION = "v24.21.0"
NPM_VERSION = "11.19.0"


class FrontendBuildError(RuntimeError):
    """A fixed, path-free frontend build failure."""


def _run(command: tuple[str, ...], *, cwd: Path) -> None:
    try:
        subprocess.run(
            command,
            cwd=cwd,
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=300,
            env=_build_environment(cwd),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise FrontendBuildError("frontend_build_failed") from error


def _version(command: tuple[str, ...], *, cwd: Path) -> str:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=_build_environment(cwd),
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise FrontendBuildError("frontend_toolchain_unavailable") from error


def _build_environment(cwd: Path) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in ("PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR")
        if name in os.environ
    }
    environment.update(
        {
            "CI": "true",
            "NO_COLOR": "1",
            "npm_config_audit": "false",
            "npm_config_cache": str(cwd.parent / "npm-cache"),
            "npm_config_fund": "false",
            "npm_config_ignore_scripts": "true",
            "npm_config_update_notifier": "false",
            "npm_config_userconfig": os.devnull,
        }
    )
    return environment


def _copy_workspace(source_root: Path, destination: Path) -> None:
    shutil.copytree(
        source_root / "frontend",
        destination / "frontend",
        ignore=shutil.ignore_patterns("node_modules", "dist", "generated"),
    )
    reference = destination / "docs" / "reference"
    reference.mkdir(parents=True)
    shutil.copy2(
        source_root / "docs" / "reference" / "review-openapi-v1.json",
        reference / "review-openapi-v1.json",
    )
    vocabulary = destination / "src" / "mechanical_librarian" / "interfaces" / "web"
    vocabulary.mkdir(parents=True)
    shutil.copy2(
        source_root
        / "src"
        / "mechanical_librarian"
        / "interfaces"
        / "web"
        / "settings_vocabulary.json",
        vocabulary / "settings_vocabulary.json",
    )


def build_frontend(source_root: Path, output: Path) -> None:
    """Build or admit one prebuilt exact bundle into a fresh destination."""
    source_root = source_root.resolve()
    prebuilt_value = os.environ.get("ML_FRONTEND_PREBUILT_DIR")
    if output.exists():
        raise FrontendBuildError("frontend_output_exists")
    output.parent.mkdir(parents=True, exist_ok=True)

    if prebuilt_value:
        prebuilt = Path(prebuilt_value).resolve()
        _validate_bundle(source_root, prebuilt)
        shutil.copytree(prebuilt, output)
        return

    if _version(("node", "--version"), cwd=source_root) != NODE_VERSION:
        raise FrontendBuildError("frontend_node_version_invalid")
    if _version(("npm", "--version"), cwd=source_root) != NPM_VERSION:
        raise FrontendBuildError("frontend_npm_version_invalid")

    with tempfile.TemporaryDirectory(
        prefix="mechanical-librarian-frontend-build-"
    ) as directory:
        root = Path(directory)
        _copy_workspace(source_root, root)
        frontend = root / "frontend"
        _run(("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"), cwd=frontend)
        _run(("npm", "run", "generate"), cwd=frontend)
        _run(("npm", "run", "typecheck"), cwd=frontend)
        _run(("npm", "run", "build"), cwd=frontend)
        built = frontend / "dist"
        _validate_bundle(source_root, built)
        shutil.copytree(built, output)


def _validate_bundle(source_root: Path, root: Path) -> None:
    try:
        specification = importlib.util.spec_from_file_location(
            "_react_asset_validation",
            source_root
            / "src"
            / "mechanical_librarian"
            / "interfaces"
            / "web"
            / "react_assets.py",
        )
        if specification is None or specification.loader is None:
            raise FrontendBuildError("frontend_bundle_invalid")
        module = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = module
        specification.loader.exec_module(module)
        bundle = module.load_react_asset_bundle(root)
        if isinstance(root, Path):
            expected = {
                "review-assets-v1.json",
                *(asset.path for asset in bundle.assets),
            }
            observed = {
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            if observed != expected or any(
                path.is_symlink() for path in root.rglob("*")
            ):
                raise FrontendBuildError("frontend_bundle_invalid")
    except Exception as error:
        raise FrontendBuildError("frontend_bundle_invalid") from error
    finally:
        sys.modules.pop("_react_asset_validation", None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    build_frontend(arguments.source_root, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
