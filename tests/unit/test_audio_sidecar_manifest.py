"""The one recognized-sidecar derivation, shared by inspection and currentness.

These run against a real directory and no probe binary: `unchanged` reads the
filesystem and never spawns a child, which is exactly the path that decides
whether a published audiobook report is still current.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.bootstrap.composition import (
    _IsolatedAudioInspector,
    _open_audio_sidecars,
    _sidecar_manifest,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioInspectionLimits,
    AudioSidecarDigest,
)

_MAXIMUM = 1_048_576


def _mount(root: Path) -> MediaMountConfiguration:
    return MediaMountConfiguration("audiobooks", str(root), "/library", True)


def _library(root: Path, documents: dict[str, bytes]) -> str:
    for name, payload in documents.items():
        (root / name).write_bytes(payload)
    return hashlib.sha256(documents["book.m4b"]).hexdigest()


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, str, tuple[AudioSidecarDigest, ...]]:
    digest = _library(
        tmp_path,
        {
            "book.m4b": b"audio-bytes",
            "book.opf": b"<package/>",
            "metadata.json": b"{}",
            "notes.txt": b"not a recognized sidecar",
        },
    )
    manifest = (
        AudioSidecarDigest("book.opf", _digest(b"<package/>")),
        AudioSidecarDigest("metadata.json", _digest(b"{}")),
    )
    return tmp_path, digest, manifest


def test_the_derivation_names_and_digests_exactly_the_recognized_sidecars(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    """`notes.txt` sits beside the recording and is not part of the set.

    A manifest that grew with every neighbouring file would make an unrelated
    edit in the same folder stale the report.
    """
    root, _, expected = library
    opened = _open_audio_sidecars(_mount(root), "book.m4b", "book.m4b")
    try:
        assert expected == _sidecar_manifest(opened)
        assert ["sidecar_opf", "sidecar_audiobookshelf"] == [
            value.surface for value in opened
        ]
    finally:
        for value in opened:
            os.close(value.descriptor)


def test_an_unchanged_library_is_current(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    root, digest, manifest = library
    assert _IsolatedAudioInspector().unchanged(
        _mount(root), "book.m4b", digest, _MAXIMUM, manifest
    )


def test_an_added_removed_or_edited_sidecar_is_not_current(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    """The audio file is byte-identical throughout; only its neighbours move.

    Each of these published a stale report before the manifest existed,
    because currentness digested the selected file and nothing else.
    """
    root, digest, manifest = library
    inspector = _IsolatedAudioInspector()

    (root / "extra.nfo").write_bytes(b"added")
    assert not inspector.unchanged(_mount(root), "book.m4b", digest, _MAXIMUM, manifest)
    (root / "extra.nfo").unlink()

    (root / "book.opf").write_bytes(b"<package edited='yes'/>")
    assert not inspector.unchanged(_mount(root), "book.m4b", digest, _MAXIMUM, manifest)
    (root / "book.opf").write_bytes(b"<package/>")

    (root / "metadata.json").unlink()
    assert not inspector.unchanged(_mount(root), "book.m4b", digest, _MAXIMUM, manifest)


def test_a_changed_audio_file_is_still_refused_before_any_sidecar_is_read(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    root, digest, manifest = library
    (root / "book.m4b").write_bytes(b"different-audio-bytes")
    assert not _IsolatedAudioInspector().unchanged(
        _mount(root), "book.m4b", digest, _MAXIMUM, manifest
    )


def test_a_sidecar_past_its_own_ceiling_is_absent_on_both_paths(
    tmp_path: Path,
) -> None:
    """The isolated child drops an oversized sidecar, so it carries no evidence.

    The manifest must agree with that, or currentness would compare against a
    document nothing ever read.
    """
    limits = AudioInspectionLimits()
    digest = _library(
        tmp_path,
        {
            "book.m4b": b"audio-bytes",
            "book.opf": b"x" * (limits.sidecar_bytes + 1),
        },
    )
    opened = _open_audio_sidecars(_mount(tmp_path), "book.m4b", "book.m4b")
    assert () == _sidecar_manifest(opened)
    assert _IsolatedAudioInspector().unchanged(
        _mount(tmp_path), "book.m4b", digest, _MAXIMUM, ()
    )
