from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetError,
    ReactAssetLoader,
    ReactAssetRecovery,
    load_react_asset_bundle,
)


def _write_bundle(root: Path) -> None:
    assets = root / "assets"
    assets.mkdir()
    values = {
        "index.html": b"<!doctype html><div id=app></div>",
        "assets/index-deadbeef.js": b"export const ready=true;",
        "assets/index-deadbeef.css": b":root{color-scheme:light}",
    }
    records = []
    for path, payload in values.items():
        destination = root.joinpath(*path.split("/"))
        destination.write_bytes(payload)
        records.append(
            {
                "kind": (
                    "shell"
                    if path == "index.html"
                    else "style" if path.endswith(".css") else "script"
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


class ReactAssetTests(unittest.TestCase):
    def test_exact_manifest_and_assets_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_bundle(root)

            bundle = load_react_asset_bundle(root)

            self.assertRegex(bundle.build_identity, r"^[0-9a-f]{64}$")
            self.assertEqual(bundle.entry, "index.html")
            self.assertIn(b"id=app", bundle.read("index.html"))

    def test_missing_corrupt_extra_shape_and_source_map_fail_closed(self) -> None:
        for mutation in (
            "missing",
            "corrupt",
            "shape",
            "source_map",
            "kind",
            "noncanonical",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
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
                with self.assertRaises((ReactAssetError, OSError)):
                    load_react_asset_bundle(root)

    def test_recovery_is_stable_and_never_requests_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loader = ReactAssetLoader(Path(directory))
            first = loader.load()
            second = loader.load()

            self.assertIs(first, second)
            self.assertIsInstance(first, ReactAssetRecovery)
            self.assertFalse(first.reload)
            self.assertEqual(first.retry, "owner_requested_restart_after_rebuild")

    def test_build_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_bundle(root)

            with self.assertRaisesRegex(
                ReactAssetError, "build_identity_mismatch"
            ):
                load_react_asset_bundle(root, "b" * 64)

    def test_manifest_identity_cannot_be_relabelled(self) -> None:
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

            with self.assertRaisesRegex(ReactAssetError, "build_identity_mismatch"):
                load_react_asset_bundle(root)


if __name__ == "__main__":
    unittest.main()
