from __future__ import annotations

import gzip
import hashlib
import json
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetBundle,
    ReactAssetError,
    ReactAssetLoader,
    ReactAssetRecovery,
    load_react_asset_bundle,
)

_SCRIPT = b"export const ready=true;"


def _write_bundle(root: Path, *, encoded: bool = False) -> None:
    assets = root / "assets"
    assets.mkdir()
    values = {
        "index.html": b"<!doctype html><html><body><div id=app></div></body></html>",
        "assets/index-deadbeef.js": _SCRIPT,
        "assets/index-deadbeef.css": b":root{color-scheme:light}",
    }
    if encoded:
        # The loader treats sibling bytes as opaque, so a marked payload stands
        # in for Brotli output that the standard library cannot produce.
        values["assets/index-deadbeef.js.br"] = b"brotli:" + _SCRIPT
        values["assets/index-deadbeef.js.gz"] = gzip.compress(_SCRIPT, mtime=0)
    records = []
    for path, payload in values.items():
        destination = root.joinpath(*path.split("/"))
        destination.write_bytes(payload)
        records.append(
            {
                "kind": (
                    "shell"
                    if path == "index.html"
                    else "br"
                    if path.endswith(".br")
                    else "gzip"
                    if path.endswith(".gz")
                    else "style"
                    if path.endswith(".css")
                    else "script"
                ),
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    records.sort(key=lambda record: record["path"])
    identity_input = json.dumps(
        {
            "schema": "review-react-assets-v1",
            "entry": "index.html",
            "assets": records,
        },
        separators=(",", ":"),
    ).encode()
    manifest = {
        "schema": "review-react-assets-v1",
        "build_identity": hashlib.sha256(identity_input).hexdigest(),
        "entry": "index.html",
        "assets": records,
    }
    (root / "review-assets-v1.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def bundle() -> Iterator[ReactAssetBundle]:
    """An exact on-disk bundle, opened, with its directory alive for reads."""

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root)
        yield load_react_asset_bundle(root)


def test_exact_manifest_and_assets_open(bundle: ReactAssetBundle) -> None:
    assert re.search(r"^[0-9a-f]{64}$", bundle.build_identity)
    assert bundle.entry == "index.html"
    assert b"id=app" in bundle.read("index.html")

    shell = bundle.shell_html("a" * 32)
    assert 'data-csrf-token="' + "a" * 32 + '"' in shell
    assert 'data-build-identity="' + bundle.build_identity + '"' in shell


@pytest.mark.parametrize(
    "hostile",
    ('" onload="alert(1)', "short", "a" * 129),
    ids=("attribute_injection", "under_length", "over_length"),
)
def test_hostile_csrf_token_is_refused_by_the_shell(
    bundle: ReactAssetBundle, hostile: str
) -> None:
    with pytest.raises(ReactAssetError):
        bundle.shell_html(hostile)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "corrupt",
        "shape",
        "source_map",
        "kind",
        "noncanonical",
    ),
)
def test_missing_corrupt_extra_shape_and_source_map_fail_closed(
    mutation: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root)
        manifest_path = root / "review-assets-v1.json"
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if mutation == "missing":
            (root / value["assets"][1]["path"]).unlink()
        elif mutation == "corrupt":
            (root / value["assets"][1]["path"]).write_bytes(b"changed")
        elif mutation == "shape":
            value["unexpected"] = True
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
        elif mutation == "source_map":
            value["assets"][1]["path"] = "assets/index.js.map"
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
        elif mutation == "kind":
            value["assets"][0]["kind"] = "script"
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
        else:
            manifest_path.write_text(
                json.dumps(value, separators=(",", ":")), encoding="utf-8"
            )
        with pytest.raises((ReactAssetError, OSError)):
            load_react_asset_bundle(root)


def test_recovery_is_stable_and_never_requests_reload() -> None:
    with tempfile.TemporaryDirectory() as directory:
        loader = ReactAssetLoader(Path(directory))
        first = loader.load()
        second = loader.load()

        assert first is second
        assert isinstance(first, ReactAssetRecovery)
        assert not first.reload
        assert first.retry == "owner_requested_restart_after_rebuild"


def test_deep_manifest_recursion_is_cached_as_stable_recovery() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "review-assets-v1.json").write_text(
            "[" * 20_000 + "]" * 20_000,
            encoding="utf-8",
        )
        loader = ReactAssetLoader(root)

        first = loader.load()
        second = loader.load()

        assert first is second
        assert isinstance(first, ReactAssetRecovery)


def test_build_identity_mismatch_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root)

        with pytest.raises(ReactAssetError, match="build_identity_mismatch"):
            load_react_asset_bundle(root, "b" * 64)


def test_manifest_identity_cannot_be_relabelled() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root)
        manifest_path = root / "review-assets-v1.json"
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["build_identity"] = "b" * 64
        manifest_path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ReactAssetError, match="build_identity_mismatch"):
            load_react_asset_bundle(root)


def _rewrite_manifest(root: Path, assets: list[dict[str, object]]) -> None:
    """Re-seal a mutated asset list so only the mutation itself is refused."""
    identity = hashlib.sha256(
        json.dumps(
            {
                "schema": "review-react-assets-v1",
                "entry": "index.html",
                "assets": assets,
            },
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    (root / "review-assets-v1.json").write_text(
        json.dumps(
            {
                "schema": "review-react-assets-v1",
                "build_identity": identity,
                "entry": "index.html",
                "assets": assets,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_encoded_siblings_negotiate_in_preference_order() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root, encoded=True)
        bundle = load_react_asset_bundle(root)
        path = "assets/index-deadbeef.js"

        def served(accept_encoding: str) -> tuple[str, bytes]:
            asset, payload = bundle.negotiate(path, accept_encoding)
            return asset.kind, payload

        assert served("gzip, deflate, br, zstd") == ("br", b"brotli:" + _SCRIPT)
        assert served("GZIP") == ("gzip", gzip.compress(_SCRIPT, mtime=0))
        assert served("br;q=0, gzip;q=0.5") == ("gzip", gzip.compress(_SCRIPT, mtime=0))
        assert served("br; q=0.000, gzip;q=0") == ("script", _SCRIPT)
        assert served("deflate, zstd") == ("script", _SCRIPT)
        assert served("") == ("script", _SCRIPT)
        # The stylesheet has no sibling, so every client receives identity.
        style, _ = bundle.negotiate("assets/index-deadbeef.css", "br, gzip")
        assert style.kind == "style"


@pytest.mark.parametrize("damage", ("missing", "corrupt"))
def test_unverifiable_sibling_falls_back_to_identity(damage: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root, encoded=True)
        sibling = root / "assets" / "index-deadbeef.js.br"
        if damage == "missing":
            sibling.unlink()
        else:
            sibling.write_bytes(b"brotli:" + _SCRIPT[::-1])

        bundle = load_react_asset_bundle(root)
        asset, payload = bundle.negotiate("assets/index-deadbeef.js", "br")

        assert (asset.kind, payload) == ("script", _SCRIPT)
        assert bundle.negotiate("assets/index-deadbeef.js", "br, gzip")[0].kind == (
            "gzip"
        )


@pytest.mark.parametrize(
    "mutation",
    ("orphan", "shell_sibling", "kind", "oversized", "unsorted", "unknown_kind"),
)
def test_sibling_manifest_shape_fails_closed(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root, encoded=True)
        value = json.loads((root / "review-assets-v1.json").read_text("utf-8"))
        assets = value["assets"]
        sibling = next(item for item in assets if item["path"].endswith(".js.br"))
        if mutation == "orphan":
            assets.remove(next(i for i in assets if i["path"].endswith(".js")))
        elif mutation == "shell_sibling":
            (root / "index.html.gz").write_bytes(b"shell")
            assets.append(
                {
                    "kind": "gzip",
                    "path": "index.html.gz",
                    "sha256": hashlib.sha256(b"shell").hexdigest(),
                    "size": 5,
                }
            )
        elif mutation == "kind":
            sibling["kind"] = "gzip"
        elif mutation == "oversized":
            sibling["size"] = 4_194_305
        elif mutation == "unsorted":
            assets.reverse()
        else:
            sibling["kind"] = "deflate"
        _rewrite_manifest(root, assets)

        with pytest.raises(ReactAssetError, match="manifest_invalid"):
            load_react_asset_bundle(root)


def test_build_identity_covers_siblings() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root, encoded=True)
        manifest_path = root / "review-assets-v1.json"
        value = json.loads(manifest_path.read_text("utf-8"))
        sibling = next(i for i in value["assets"] if i["path"].endswith(".gz"))
        sibling["sha256"] = "0" * 64
        manifest_path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        with pytest.raises(ReactAssetError, match="build_identity_mismatch"):
            load_react_asset_bundle(root)


def test_served_bytes_are_verified_once_and_never_rehashed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root, encoded=True)
        bundle = load_react_asset_bundle(root)
        # Bytes on disk can no longer reach a client once verified at load.
        (root / "assets" / "index-deadbeef.js").write_bytes(b"replaced")

        with patch(
            "mechanical_librarian.interfaces.web.react_assets.hashlib.sha256",
            side_effect=AssertionError("rehashed"),
        ):
            for accept_encoding in ("br", "gzip", ""):
                bundle.negotiate("assets/index-deadbeef.js", accept_encoding)
            assert bundle.read("assets/index-deadbeef.js") == _SCRIPT
            bundle.shell_html("a" * 32)


def test_corrupt_identity_asset_keeps_its_existing_refusal() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_bundle(root, encoded=True)
        (root / "assets" / "index-deadbeef.js").write_bytes(b"export const x=1;")

        with pytest.raises(ReactAssetError, match="asset_corrupt"):
            load_react_asset_bundle(root)
