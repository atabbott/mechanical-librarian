"""The one recognized-sidecar derivation, shared by inspection and currentness.

These run against a real directory and no probe binary: `unchanged` reads the
filesystem and never spawns a child, which is exactly the path that decides
whether a published audiobook report is still current.  Every name and byte
here is synthetic.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
    open_audio_sidecars,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.domain.audio_inspection import (
    AudioInspectionLimits,
    AudioSidecarBinding,
    AudioSidecarDigest,
    AudioSidecarKind,
    AudioSidecarRefusal,
)

_MAXIMUM = 1_048_576
_OWN = ("book.m4b", "book.opf", "metadata.json", "notes.txt")


def _mount(root: Path) -> MediaMountConfiguration:
    return MediaMountConfiguration("audiobooks", str(root), "/library", True)


def _library(root: Path, documents: dict[str, bytes]) -> str:
    for name, payload in documents.items():
        (root / name).write_bytes(payload)
    return hashlib.sha256(documents.get("book.m4b", b"")).hexdigest()


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _derived(
    root: Path,
    own: tuple[str, ...] | None,
    media: tuple[str, ...] = ("book.m4b",),
) -> list[tuple[str, str, str, str | None]]:
    """Open, classify and close the derived set, as name/kind/binding/refusal."""
    opened = open_audio_sidecars(_mount(root), media[0], media, own)
    try:
        return [
            (
                value.name,
                value.kind.value,
                value.binding.value,
                None if value.refusal is None else value.refusal.value,
            )
            for value in opened
        ]
    finally:
        for value in opened:
            os.close(value.descriptor)


def _entry(
    name: str,
    payload: bytes,
    kind: AudioSidecarKind,
    binding: AudioSidecarBinding,
    *refusals: AudioSidecarRefusal,
) -> AudioSidecarDigest:
    return AudioSidecarDigest(name, _digest(payload), kind, binding, refusals)


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
        _entry(
            "book.opf",
            b"<package/>",
            AudioSidecarKind.OPF,
            AudioSidecarBinding.TRACK,
        ),
        _entry(
            "metadata.json",
            b"{}",
            AudioSidecarKind.AUDIOBOOKSHELF_RECORD,
            AudioSidecarBinding.FOLDER,
            AudioSidecarRefusal.TARGET_RECORD,
        ),
    )
    return tmp_path, digest, manifest


def test_the_derivation_classifies_exactly_the_recognized_documents(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    """`notes.txt` is not part of the set; the target's record is refused.

    T1 and S12: Audiobookshelf's `metadata.json` is digested and refused
    `target_record`, so it never reaches a child.
    """
    root, _, _ = library
    assert _derived(root, _OWN) == [
        ("book.opf", "opf", "track", None),
        ("metadata.json", "audiobookshelf_record", "folder", "target_record"),
    ]


def test_an_unchanged_library_is_current(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    root, digest, manifest = library
    assert IsolatedAudioInspector().unchanged(
        _mount(root), ("book.m4b",), digest, _MAXIMUM, manifest, own_file_names=_OWN
    )


def test_an_added_removed_or_edited_sidecar_is_not_current(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    """The audio file is byte-identical throughout; only its neighbours move.

    Editing the refused record makes the report stale too (T5d).
    """
    root, digest, manifest = library
    inspector = IsolatedAudioInspector()

    def current() -> bool:
        return inspector.unchanged(
            _mount(root),
            ("book.m4b",),
            digest,
            _MAXIMUM,
            manifest,
            own_file_names=_OWN,
        )

    (root / "extra.nfo").write_bytes(b"added")
    assert not current()
    (root / "extra.nfo").unlink()
    assert current()

    (root / "book.opf").write_bytes(b"<package edited='yes'/>")
    assert not current()
    (root / "book.opf").write_bytes(b"<package/>")

    (root / "metadata.json").write_bytes(b'{"edited": true}')
    assert not current()
    (root / "metadata.json").write_bytes(b"{}")
    assert current()

    (root / "metadata.json").unlink()
    assert not current()


def test_a_changed_audio_file_is_still_refused_before_any_sidecar_is_read(
    library: tuple[Path, str, tuple[AudioSidecarDigest, ...]],
) -> None:
    root, digest, manifest = library
    (root / "book.m4b").write_bytes(b"different-audio-bytes")
    assert not IsolatedAudioInspector().unchanged(
        _mount(root), ("book.m4b",), digest, _MAXIMUM, manifest, own_file_names=_OWN
    )


def test_a_sidecar_past_its_own_ceiling_is_absent_on_both_paths(
    tmp_path: Path,
) -> None:
    """S2 and T26 (guard): a document of 1 MiB + 1 is absent; 1 MiB is read."""
    limits = AudioInspectionLimits()
    digest = _library(
        tmp_path,
        {
            "book.m4b": b"audio-bytes",
            "book.opf": b"x" * (limits.sidecar_bytes + 1),
            "book.nfo": b"x" * limits.sidecar_bytes,
        },
    )
    own = ("book.m4b", "book.opf", "book.nfo")
    assert _derived(tmp_path, own) == [("book.nfo", "nfo", "track", None)]
    assert IsolatedAudioInspector().unchanged(
        _mount(tmp_path),
        ("book.m4b",),
        digest,
        _MAXIMUM,
        (
            _entry(
                "book.nfo",
                b"x" * limits.sidecar_bytes,
                AudioSidecarKind.NFO,
                AudioSidecarBinding.TRACK,
            ),
        ),
        own_file_names=own,
    )


@pytest.mark.parametrize(
    "foreign",
    ("other.m4b", "other.flac", "other.pdf", "Other.MOBI"),
    ids=("m4b", "flac", "pdf", "mobi_upper"),
)
def test_a_folder_level_sidecar_beside_another_items_file_is_shared_folder(
    tmp_path: Path, foreign: str
) -> None:
    """T5, T5b, T5c: another item's audio or ebook file shares the folder."""
    _library(
        tmp_path,
        {"book.m4b": b"audio", "folder.opf": b"<package/>", foreign: b"x"},
    )
    assert _derived(tmp_path, ("book.m4b", "folder.opf")) == [
        ("folder.opf", "opf", "folder", "shared_folder")
    ]


def test_the_items_own_files_do_not_make_a_folder_shared(tmp_path: Path) -> None:
    """T6, T6b: an excluded track, a companion EPUB and a second PDF are own."""
    names = {
        "book.m4b": b"audio",
        "excluded.mp3": b"audio",
        "companion.epub": b"epub",
        "supplement.pdf": b"pdf",
        "cover.jpg": b"image",
        "folder.opf": b"<package/>",
    }
    _library(tmp_path, names)
    assert _derived(tmp_path, tuple(names)) == [("folder.opf", "opf", "folder", None)]


def test_an_unknown_own_file_list_fails_closed(tmp_path: Path) -> None:
    """T6c: a malformed or over-bound list reaches here as `None`."""
    _library(tmp_path, {"book.m4b": b"audio", "folder.opf": b"<package/>"})
    assert _derived(tmp_path, None) == [
        ("folder.opf", "opf", "folder", "shared_folder")
    ]


def test_a_track_named_sidecar_in_a_shared_folder_is_still_the_tracks(
    tmp_path: Path,
) -> None:
    """T7: `<track stem>.cue` is bound to its track, so sharing does not apply."""
    _library(
        tmp_path,
        {"book.m4b": b"audio", "book.cue": b"FILE x", "other.m4b": b"audio"},
    )
    assert _derived(tmp_path, ("book.m4b", "book.cue")) == [
        ("book.cue", "cue", "track", None)
    ]


def test_a_foreign_file_added_beside_a_folder_level_sidecar_is_not_current(
    tmp_path: Path,
) -> None:
    """S13: currentness recomputes the parent-side refusal from the listing."""
    digest = _library(tmp_path, {"book.m4b": b"audio", "folder.opf": b"<package/>"})
    own = ("book.m4b", "folder.opf")
    manifest = (
        _entry(
            "folder.opf",
            b"<package/>",
            AudioSidecarKind.OPF,
            AudioSidecarBinding.FOLDER,
        ),
    )
    inspector = IsolatedAudioInspector()
    assert inspector.unchanged(
        _mount(tmp_path), ("book.m4b",), digest, _MAXIMUM, manifest, own_file_names=own
    )

    (tmp_path / "other.flac").write_bytes(b"another item")

    assert not inspector.unchanged(
        _mount(tmp_path), ("book.m4b",), digest, _MAXIMUM, manifest, own_file_names=own
    )


def test_nine_documents_give_eight_entries(tmp_path: Path) -> None:
    """T24 (guard): the document bound is unchanged."""
    names = {"book.m4b": b"audio"} | {f"f{index}.nfo": b"x" for index in range(9)}
    _library(tmp_path, names)
    assert len(_derived(tmp_path, tuple(names))) == 8


def test_other_items_documents_do_not_crowd_out_the_items_own(tmp_path: Path) -> None:
    """T24b: track-bound documents open first, then the record, then the rest."""
    names = {
        "zz-book.m4b": b"audio",
        "zz-book.cue": b"FILE x",
        "metadata.json": b"{}",
    } | {f"a{index}.opf": b"<package/>" for index in range(9)}
    _library(tmp_path, names)
    derived = _derived(tmp_path, tuple(names), ("zz-book.m4b",))
    assert derived[:2] == [
        ("zz-book.cue", "cue", "track", None),
        ("metadata.json", "audiobookshelf_record", "folder", "target_record"),
    ]
    assert len(derived) == 8


def test_a_listing_past_its_bound_gives_no_sidecars(tmp_path: Path) -> None:
    """S9 and T25 (guard): 1,025 entries give an empty candidate set."""
    _library(tmp_path, {"book.m4b": b"audio", "book.opf": b"<package/>"})
    for index in range(1_023):
        (tmp_path / f"filler-{index:04d}.txt").write_bytes(b"")
    assert _derived(tmp_path, None) == []


@pytest.fixture
def links(tmp_path: Path) -> Iterator[Path]:
    _library(tmp_path, {"book.m4b": b"audio", "target.txt": b"<package/>"})
    os.symlink(tmp_path / "target.txt", tmp_path / "linked.opf")
    os.mkfifo(tmp_path / "pipe.nfo")
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "linked-dir.cue")
    yield tmp_path


def test_symlinks_fifos_and_linked_directories_are_never_opened(links: Path) -> None:
    """S5 (guard): nothing hangs, and none of them is in the set."""
    assert _derived(links, None) == []
