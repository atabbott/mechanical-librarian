from __future__ import annotations

import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest
from scripts.verify_playwright_browser import (
    BrowserVerificationError,
    browser_tree_sha256,
    cli,
)


def test_tree_digest_binds_paths_sizes_and_contents() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "nested").mkdir()
        (root / "browser").write_bytes(b"executable")
        (root / "nested" / "data.pak").write_bytes(b"data")
        first = browser_tree_sha256(root)
        assert first == browser_tree_sha256(root)
        (root / "nested" / "data.pak").write_bytes(b"changed")
        assert first != browser_tree_sha256(root)


def test_tree_digest_rejects_empty_or_symlinked_payload() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with pytest.raises(BrowserVerificationError):
            browser_tree_sha256(root)
        target = root / "browser"
        target.write_bytes(b"executable")
        (root / "alias").symlink_to(target)
        with pytest.raises(BrowserVerificationError):
            browser_tree_sha256(root)


def test_cli_failure_is_path_free_and_bounded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        private_path = Path(directory) / "missing"
        diagnostics = StringIO()
        with redirect_stderr(diagnostics):
            result = cli(
                ("--path", str(private_path), "--platform", "darwin-arm64")
            )
    assert result == 1
    assert '"reason":"browser_verification_failed"' in diagnostics.getvalue()
    assert str(private_path) not in diagnostics.getvalue()
