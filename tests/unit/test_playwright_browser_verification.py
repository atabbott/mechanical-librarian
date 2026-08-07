from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from scripts.verify_playwright_browser import (
    BrowserVerificationError,
    browser_tree_sha256,
    cli,
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

    def test_cli_failure_is_path_free_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private_path = Path(directory) / "missing"
            diagnostics = StringIO()
            with redirect_stderr(diagnostics):
                result = cli(
                    ("--path", str(private_path), "--platform", "darwin-arm64")
                )
        self.assertEqual(result, 1)
        self.assertIn('"reason":"browser_verification_failed"', diagnostics.getvalue())
        self.assertNotIn(str(private_path), diagnostics.getvalue())


if __name__ == "__main__":
    unittest.main()
