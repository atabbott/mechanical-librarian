import hashlib
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.evidence_extractors import epub_cover
from mechanical_librarian.adapters.evidence_extractors.epub_cover import (
    read_declared_cover,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceState
from tests.support.epub_packages import EPUB3_PACKAGE, epub_bytes


class _TimeoutProcess:
    pid = 999_999
    stdin = None
    stdout = None
    stderr = None
    returncode: int | None = None

    def communicate(
        self,
        _context: bytes,
        *,
        timeout: float,
    ) -> tuple[bytes, bytes]:
        raise subprocess.TimeoutExpired("epub-child", timeout)


def test_reads_only_declared_cover_member() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "book.epub"
        path.write_bytes(epub_bytes())
        descriptor = os.open(path, os.O_RDONLY)
        try:
            result = read_declared_cover(
                descriptor, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        finally:
            os.close(descriptor)
        assert result.state is None
        assert b"TRAP: cover bytes must remain opaque" == result.data


def test_absent_cover_is_not_heuristically_selected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "book.epub"
        package = EPUB3_PACKAGE.replace(b' properties="cover-image"', b"")
        path.write_bytes(epub_bytes(package=package))
        descriptor = os.open(path, os.O_RDONLY)
        try:
            result = read_declared_cover(
                descriptor, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        finally:
            os.close(descriptor)
        assert CoverSourceState.ABSENT == result.state


def test_exact_eight_mebibyte_cover_uses_bounded_binary_pipe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "book.epub"
        cover = b"x" * (8 * 1024 * 1024)
        path.write_bytes(
            epub_bytes(
                include_default_content=False,
                extra_entries=(
                    ("EPUB/nav.xhtml", b"nav", zipfile.ZIP_DEFLATED),
                    ("EPUB/chapter.xhtml", b"chapter", zipfile.ZIP_DEFLATED),
                    ("EPUB/cover.jpg", cover, zipfile.ZIP_STORED),
                ),
            )
        )
        descriptor = os.open(path, os.O_RDONLY)
        try:
            result = read_declared_cover(
                descriptor, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        finally:
            os.close(descriptor)
        assert result.state is None
        assert cover == result.data


def test_repeated_child_timeouts_close_every_parent_pipe() -> None:
    before = len(os.listdir("/dev/fd"))
    process = _TimeoutProcess()
    with (
        patch.object(epub_cover.subprocess, "Popen", return_value=process),
        patch.object(epub_cover, "_stop") as stop,
    ):
        for _ in range(20):
            result = read_declared_cover(-1, "0" * 64)
            assert CoverSourceState.TIMEOUT == result.state
    assert 20 == stop.call_count
    assert before == len(os.listdir("/dev/fd"))


@pytest.mark.parametrize(
    "name,data,expected",
    (
        ("EPUB/", b"", None),
        ("../", b"", CoverSourceState.INVALID_DECLARATION),
        ("../cover.jpg", b"", CoverSourceState.INVALID_DECLARATION),
        ("EPUB/", b"not empty", CoverSourceState.INVALID_DECLARATION),
        ("EPUB/cover.jpg", b"duplicate", CoverSourceState.INVALID_DECLARATION),
    ),
)
def test_directory_entries_preserve_validated_single_file_selection(
    tmp_path: Path, name: str, data: bytes, expected: CoverSourceState | None
) -> None:
    path = tmp_path / "book.epub"
    path.write_bytes(epub_bytes(extra_entries=((name, data, zipfile.ZIP_STORED),)))
    with path.open("rb") as source:
        result = read_declared_cover(
            source.fileno(), hashlib.file_digest(source, "sha256").hexdigest()
        )
    assert result.state is expected
    if expected is None:
        assert result.data == b"TRAP: cover bytes must remain opaque"


def test_child_cancellation_closes_parent_pipes_without_reclassification() -> None:
    before = len(os.listdir("/dev/fd"))
    interruption = KeyboardInterrupt()
    with (
        patch.object(epub_cover.subprocess, "Popen", return_value=_TimeoutProcess()),
        patch.object(_TimeoutProcess, "communicate", side_effect=interruption),
        patch.object(epub_cover, "_stop") as stop,
        pytest.raises(KeyboardInterrupt) as raised,
    ):
        read_declared_cover(-1, "0" * 64)
    assert raised.value is interruption
    stop.assert_called_once()
    assert before == len(os.listdir("/dev/fd"))


@pytest.mark.parametrize(
    "package,expected",
    (
        (
            EPUB3_PACKAGE.replace(
                b'media-type="image/jpeg"', b'media-type="image/svg+xml"'
            ),
            CoverSourceState.UNSUPPORTED_FORMAT,
        ),
        (
            EPUB3_PACKAGE.replace(
                b"</manifest>",
                b'<item id="other-cover" href="other.jpg" media-type="image/jpeg" properties="cover-image"/></manifest>',
            ),
            CoverSourceState.ABSENT,
        ),
    ),
)
def test_unsupported_and_ambiguous_cover_declarations_are_not_decoded(
    tmp_path, package, expected
):
    path = tmp_path / "book.epub"
    path.write_bytes(
        epub_bytes(
            package=package,
            extra_entries=(
                ("EPUB/", b"", zipfile.ZIP_STORED),
                ("EPUB/other.jpg", b"other", zipfile.ZIP_STORED),
            ),
        )
    )
    with path.open("rb") as source:
        result = read_declared_cover(
            source.fileno(), hashlib.file_digest(source, "sha256").hexdigest()
        )
    assert result.state is expected
    assert result.data is None


def test_malformed_child_protocol_remains_invalid_declaration():
    process = _TimeoutProcess()
    process.returncode = 0
    with (
        patch.object(epub_cover.subprocess, "Popen", return_value=process),
        patch.object(
            _TimeoutProcess, "communicate", return_value=(b'{"state":"made-up"}', b"")
        ),
        patch.object(epub_cover, "_stop") as stop,
    ):
        result = read_declared_cover(-1, "0" * 64)
    assert result.state is CoverSourceState.INVALID_DECLARATION
    stop.assert_called_once()
