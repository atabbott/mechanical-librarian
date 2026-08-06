from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.verify_playwright_browser import (
    BrowserVerificationError,
    browser_tree_sha256,
)


class PlaywrightBrowserVerificationTests(unittest.TestCase):
    def test_tree_digest_binds_paths_sizes_and_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "browser").write_bytes(b"executable")
            (root / "nested" / "data.pak").write_bytes(b"data")
            first = browser_tree_sha256(root)
            self.assertEqual(first, browser_tree_sha256(root))
            (root / "nested" / "data.pak").write_bytes(b"changed")
            self.assertNotEqual(first, browser_tree_sha256(root))

    def test_tree_digest_rejects_empty_or_symlinked_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(BrowserVerificationError):
                browser_tree_sha256(root)
            target = root / "browser"
            target.write_bytes(b"executable")
            (root / "alias").symlink_to(target)
            with self.assertRaises(BrowserVerificationError):
                browser_tree_sha256(root)


if __name__ == "__main__":
    unittest.main()
