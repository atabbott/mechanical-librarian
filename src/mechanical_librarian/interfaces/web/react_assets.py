"""Bounded access to one deterministically built, currently unactivated shell."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Protocol

_BUILD_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
_CSRF_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_ASSET_PATH = re.compile(
    r"^(?:index\.html|assets/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(?:css|js))$"
)
_MAX_MANIFEST_BYTES = 65_536
_MAX_ASSETS = 128
_MAX_ASSET_BYTES = 4_194_304
_MAX_TOTAL_BYTES = 16_777_216


class ReactAssetError(RuntimeError):
    """A fixed, value-free packaged-asset failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _AssetFile(Protocol):
    def read_bytes(self) -> bytes: ...


class _AssetRoot(Protocol):
    def joinpath(self, *descendants: str) -> _AssetFile: ...


@dataclass(frozen=True, slots=True)
class ReactAsset:
    path: str
    sha256: str
    size: int
    kind: str


@dataclass(frozen=True, slots=True)
class ReactAssetBundle:
    build_identity: str
    entry: str
    assets: tuple[ReactAsset, ...]
    _root: _AssetRoot

    def read(self, path: str) -> bytes:
        selected = next((asset for asset in self.assets if asset.path == path), None)
        if selected is None:
            raise ReactAssetError("asset_not_found")
        payload = self._root.joinpath(*path.split("/")).read_bytes()
        if (
            len(payload) != selected.size
            or hashlib.sha256(payload).hexdigest() != selected.sha256
        ):
            raise ReactAssetError("asset_corrupt")
        return payload

    def shell_html(self, csrf_token: str) -> str:
        if _CSRF_TOKEN.fullmatch(csrf_token) is None:
            raise ReactAssetError("shell_invalid")
        try:
            shell = self.read(self.entry).decode("utf-8", "strict")
        except (OSError, UnicodeError) as error:
            raise ReactAssetError("shell_invalid") from error
        marker = "<body>"
        if (
            shell.count(marker) != 1
            or "data-csrf-token" in shell
            or "data-build-identity" in shell
        ):
            raise ReactAssetError("shell_invalid")
        return shell.replace(
            marker,
            '<body data-csrf-token="'
            + csrf_token
            + '" data-build-identity="'
            + self.build_identity
            + '">',
            1,
        )


@dataclass(frozen=True, slots=True)
class ReactAssetRecovery:
    """Stable recovery state; callers must not instruct automatic reload."""

    code: str = "react_assets_unavailable"
    retry: str = "owner_requested_restart_after_rebuild"
    reload: bool = False


class ReactAssetLoader:
    """Load once so a broken installation cannot cause a reload loop."""

    def __init__(
        self,
        root: _AssetRoot | None = None,
        expected_build_identity: str | None = None,
    ) -> None:
        self._packaged = root is None
        self._root = root or files("mechanical_librarian.interfaces.web").joinpath(
            "react_assets"
        )
        self._expected_build_identity = expected_build_identity
        self._result: ReactAssetBundle | ReactAssetRecovery | None = None

    def load(self) -> ReactAssetBundle | ReactAssetRecovery:
        if self._result is None:
            try:
                expected = self._expected_build_identity
                if expected is None and self._packaged:
                    expected = _installed_build_identity()
                self._result = load_react_asset_bundle(self._root, expected)
            except (OSError, ReactAssetError, RecursionError, UnicodeError, ValueError):
                self._result = ReactAssetRecovery()
        return self._result


def load_react_asset_bundle(
    root: _AssetRoot | Path, expected_build_identity: str | None = None
) -> ReactAssetBundle:
    manifest_path = root.joinpath("review-assets-v1.json")
    raw = manifest_path.read_bytes()
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise ReactAssetError("manifest_invalid")
    try:
        text = raw.decode("utf-8", "strict")
        value = json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReactAssetError("manifest_invalid") from error
    if (
        text
        != json.dumps(value, indent=2, separators=(",", ": "), ensure_ascii=False)
        + "\n"
    ):
        raise ReactAssetError("manifest_invalid")
    if (
        type(value) is not dict
        or set(value) != {"assets", "build_identity", "entry", "schema"}
        or value["schema"] != "review-react-assets-v1"
        or type(value["build_identity"]) is not str
        or _BUILD_IDENTITY.fullmatch(value["build_identity"]) is None
        or value["entry"] != "index.html"
        or type(value["assets"]) is not list
        or not 1 <= len(value["assets"]) <= _MAX_ASSETS
    ):
        raise ReactAssetError("manifest_invalid")
    if (
        expected_build_identity is not None
        and value["build_identity"] != expected_build_identity
    ):
        raise ReactAssetError("build_identity_mismatch")

    assets: list[ReactAsset] = []
    seen: set[str] = set()
    total = 0
    for item in value["assets"]:
        if (
            type(item) is not dict
            or set(item) != {"kind", "path", "sha256", "size"}
            or type(item["path"]) is not str
            or _ASSET_PATH.fullmatch(item["path"]) is None
            or item["path"] in seen
            or type(item["sha256"]) is not str
            or _BUILD_IDENTITY.fullmatch(item["sha256"]) is None
            or type(item["size"]) is not int
            or not 0 < item["size"] <= _MAX_ASSET_BYTES
            or item["kind"] not in {"shell", "script", "style"}
        ):
            raise ReactAssetError("manifest_invalid")
        if (item["path"] == "index.html") != (item["kind"] == "shell"):
            raise ReactAssetError("manifest_invalid")
        if item["path"].endswith(".css") != (item["kind"] == "style"):
            raise ReactAssetError("manifest_invalid")
        if item["path"].endswith(".js") != (item["kind"] == "script"):
            raise ReactAssetError("manifest_invalid")
        seen.add(item["path"])
        total += item["size"]
        assets.append(
            ReactAsset(item["path"], item["sha256"], item["size"], item["kind"])
        )
    if (
        "index.html" not in seen
        or total > _MAX_TOTAL_BYTES
        or [asset.path for asset in assets] != sorted(seen)
    ):
        raise ReactAssetError("manifest_invalid")

    identity_input = json.dumps(
        {
            "schema": "review-react-assets-v1",
            "entry": "index.html",
            "assets": value["assets"],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(identity_input).hexdigest() != value["build_identity"]:
        raise ReactAssetError("build_identity_mismatch")

    bundle = ReactAssetBundle(
        value["build_identity"], value["entry"], tuple(assets), root
    )
    for asset in bundle.assets:
        bundle.read(asset.path)
    return bundle


def _installed_build_identity() -> str:
    try:
        from mechanical_librarian.interfaces.web.react_asset_identity import (  # type: ignore[import-not-found]  # reason: module is generated at package build time by setup.py and is absent from the source tree.
            EXPECTED_REACT_BUILD_IDENTITY,
        )
    except ImportError as error:
        raise ReactAssetError("build_identity_unavailable") from error
    if (
        type(EXPECTED_REACT_BUILD_IDENTITY) is not str
        or _BUILD_IDENTITY.fullmatch(EXPECTED_REACT_BUILD_IDENTITY) is None
    ):
        raise ReactAssetError("build_identity_unavailable")
    return EXPECTED_REACT_BUILD_IDENTITY


__all__ = [
    "ReactAsset",
    "ReactAssetBundle",
    "ReactAssetError",
    "ReactAssetLoader",
    "ReactAssetRecovery",
    "load_react_asset_bundle",
]
