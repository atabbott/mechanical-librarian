"""Characterization of the bounded no-follow sibling listing.

The listing is what finds an audiobook's sidecars, so it walks a directory an
owner controls the contents of. Every case here is about what that directory
can do to the walk: how large it is allowed to get before the walk refuses, and
what it is never allowed to make the walk follow.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.evidence_extractors.local_media import (
    LocalMediaError,
    LocalMediaLimitError,
    list_local_media_siblings,
)


@pytest.fixture
def mount() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temporary:
        yield Path(temporary).resolve()


def _fill(folder: Path, count: int) -> None:
    for index in range(count):
        (folder / f"entry-{index:05d}.txt").write_text("x", encoding="utf-8")


@pytest.mark.parametrize("count", (1_023, 1_024))
def test_a_directory_at_or_under_the_bound_lists_every_entry(
    mount: Path, count: int
) -> None:
    """The bound admits a folder holding a multi-file audiobook.

    At 256 the sidecar derivation lost every document beside a 352-track
    recording, because it reads a refused listing as an absent candidate set.
    """
    _fill(mount, count)

    names = list_local_media_siblings(str(mount), "entry-00000.txt")

    assert len(names) == count
    assert names[0] == "entry-00000.txt"


def test_the_entry_past_the_bound_is_where_the_listing_refuses(mount: Path) -> None:
    _fill(mount, 1_025)

    with pytest.raises(LocalMediaLimitError):
        list_local_media_siblings(str(mount), "entry-00000.txt")


def test_the_refusal_is_a_limit_rather_than_an_unavailable_source(
    mount: Path,
) -> None:
    """`LocalMediaError` is an `OSError`, so the two are easy to conflate."""
    _fill(mount, 3)

    with pytest.raises(LocalMediaLimitError):
        list_local_media_siblings(str(mount), "entry-00000.txt", maximum_entries=2)


def test_the_listing_is_sorted_and_the_same_every_time(mount: Path) -> None:
    for name in ("zulu.m4b", "alpha.nfo", "Mike.opf", "book.cue"):
        (mount / name).write_text("x", encoding="utf-8")

    first = list_local_media_siblings(str(mount), "book.cue")
    second = list_local_media_siblings(str(mount), "book.cue")

    assert first == ("Mike.opf", "alpha.nfo", "book.cue", "zulu.m4b")
    assert first == second


def test_a_symlinked_component_is_never_walked(mount: Path) -> None:
    real = mount / "real"
    real.mkdir()
    (real / "book.m4b").write_text("x", encoding="utf-8")
    os.symlink(real, mount / "link")

    with pytest.raises(LocalMediaError):
        list_local_media_siblings(str(mount), "link/book.m4b")


def test_a_symlink_among_the_siblings_is_named_but_not_resolved(mount: Path) -> None:
    (mount / "book.m4b").write_text("x", encoding="utf-8")
    os.symlink(mount / "book.m4b", mount / "shortcut.m4b")

    names = list_local_media_siblings(str(mount), "book.m4b")

    assert names == ("book.m4b", "shortcut.m4b")


def test_a_relative_mount_is_refused_before_anything_is_opened() -> None:
    with pytest.raises(LocalMediaError):
        list_local_media_siblings("relative/mount", "book.m4b")


@pytest.mark.parametrize("maximum", (0, 4_097, "256"))
def test_a_listing_limit_outside_its_range_is_refused(
    mount: Path, maximum: object
) -> None:
    with pytest.raises(LocalMediaError):
        list_local_media_siblings(
            str(mount),
            "book.m4b",
            maximum_entries=maximum,  # type: ignore[arg-type]
        )
