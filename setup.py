"""Setuptools hook for exact out-of-tree frontend asset generation."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).parent.resolve()


class BuildWithFrontend(build_py):
    def run(self) -> None:
        package_root = Path(self.build_lib).resolve() / "mechanical_librarian"
        if package_root.exists():
            shutil.rmtree(package_root)
        super().run()
        specification = importlib.util.spec_from_file_location(
            "build_frontend", ROOT / "scripts" / "build_frontend.py"
        )
        if specification is None or specification.loader is None:
            raise RuntimeError("frontend build unavailable")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        asset_root = (
            Path(self.build_lib)
            / "mechanical_librarian"
            / "interfaces"
            / "web"
            / "react_assets"
        )
        module.build_frontend(
            ROOT,
            asset_root,
        )
        manifest = json.loads(
            (asset_root / "review-assets-v1.json").read_text(encoding="utf-8")
        )
        identity = manifest["build_identity"]
        if type(identity) is not str or len(identity) != 64:
            raise RuntimeError("frontend build identity invalid")
        (asset_root.parent / "react_asset_identity.py").write_text(
            '"""Generated exact frontend build identity."""\n\n'
            f'EXPECTED_REACT_BUILD_IDENTITY = "{identity}"\n',
            encoding="utf-8",
        )


setup(cmdclass={"build_py": BuildWithFrontend})
