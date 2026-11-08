"""One hostile 1 MiB sidecar per recognition pattern, through the real child.

S8 and L1 to L7 (#1481).  Each document is built to make one pattern
backtrack: the `<meta` and element scanners (L1, L2, which main's patterns
could not finish inside the child's limits), the attribute tokenizer, the NFO
and CUE line grammars, the sequence shape and the ASIN token.  Each goes
through the real probe and interpretation children under their own CPU and
wall limits, and the item still inspects successfully.  Every byte here is
synthetic.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    resolve_probe_path,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    TargetFile,
)
from mechanical_librarian.domain.audio_inspection import AudioInspectionLimits
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
)
from tests.support.audio_containers import Mp4Fixture, mp4_audiobook

_LIMIT = AudioInspectionLimits().sidecar_bytes


_BOM = "\ufeff"


def _fill(head: str, unit: str, tail: str = "") -> str:
    """`head`, then `unit` repeated, then `tail`, at most 1 MiB in all."""
    room = _LIMIT - len(head.encode()) - len(tail.encode())
    return head + unit * (room // len(unit.encode())) + tail


_HOSTILE = (
    # L1: many `<meta name="…"` heads, none ever closing.
    ("book.opf", _fill("<package>", '<meta name="calibre:series" ')),
    # L2: many element heads with no `>`.
    ("book.opf", _fill("<package>", "<dc:title ")),
    # L3: one maximal attribute run per element, repeated.
    (
        "book.opf",
        _fill(
            "<package>",
            "<dc:contributor " + 'a="b" ' * 680 + 'opf:role="nrt">x</dc:contributor>',
        ),
    ),
    # L4: one NFO line that never states its colon.
    ("book.nfo", _fill("", "Narrator ")),
    # L5: one CUE line of a key and spaces with nothing after them.
    ("book.cue", _fill("REM ", "A" * 31 + " ")),
    # L6: a sequence value far past its shape.
    ("book.nfo", _fill("Series Part: ", "1")),
    # L7: an ASIN value far past its token.
    ("book.metadata.json", _fill('{"asin": "', "B", '"}')),
)


@pytest.fixture
def media_root() -> Iterator[Path]:
    if resolve_probe_path() is None:
        raise AssertionError("this tier executes the shipped probe and found none")
    with tempfile.TemporaryDirectory() as temporary:
        yield Path(temporary).resolve()


_IDS = (
    "L1_meta",
    "L2_element",
    "L3_attribute",
    "L4_nfo_line",
    "L5_cue_line",
    "L6_sequence",
    "L7_asin",
)
# #1796: each hostile document again, opening with one byte-order mark.  The
# mark takes three bytes, so the document is cut by as many to stay inside the
# 1 MiB bound.
_MARKED = tuple(
    (name, _BOM + document.encode()[: _LIMIT - 3].decode("utf-8", "ignore"))
    for name, document in _HOSTILE
)


@pytest.mark.parametrize(
    ("name", "document"),
    _HOSTILE + _MARKED,
    ids=_IDS + tuple(f"{value}_bom" for value in _IDS),
)
def test_a_hostile_document_cannot_stall_recognition(
    media_root: Path, name: str, document: str
) -> None:
    assert len(document.encode()) <= _LIMIT
    folder = media_root / "Item"
    folder.mkdir()
    (folder / "book.m4b").write_bytes(
        mp4_audiobook(Mp4Fixture(well_known={"CDEK": "B0SYNTH001"}))
    )
    (folder / name).write_text(document, encoding="utf-8")

    started = time.monotonic()
    result = IsolatedAudioInspector().inspect(
        MediaMountConfiguration("audiobooks", str(media_root), "/library", True),
        (TargetFile("1" * 64, "Item/book.m4b", "m4b", 1),),
        268_435_456,
        association=AudioAssociationContext(None, (), ("book.m4b", name)),
    )
    elapsed = time.monotonic() - started

    assert ("asin", "B0SYNTH001", "embedded_tag") in {
        (value.kind.value, value.observed, value.basis) for value in result.claims
    }
    assert [value.name for value in result.audio.sidecars] == [name]
    # Well inside the child's 30 s CPU limit: recognition is linear.
    assert elapsed < 15, elapsed
    assert json.dumps(result.audio.to_data())
    [entry] = result.audio.sidecars
    assert (entry.byte_order_mark is not None) is document.startswith(_BOM)


def _inspect_item(media_root: Path, documents: dict[str, str]) -> AudioInspectionResult:
    folder = media_root / "Item"
    folder.mkdir()
    (folder / "book.m4b").write_bytes(
        mp4_audiobook(Mp4Fixture(well_known={"CDEK": "B0SYNTH001"}))
    )
    for name, document in documents.items():
        assert len(document.encode()) <= _LIMIT
        (folder / name).write_text(document, encoding="utf-8")
    return IsolatedAudioInspector().inspect(
        MediaMountConfiguration("audiobooks", str(media_root), "/library", True),
        (TargetFile("1" * 64, "Item/book.m4b", "m4b", 1),),
        268_435_456,
        association=AudioAssociationContext(None, (), ("book.m4b", *documents)),
    )


_EIGHT = (
    "book.metadata.json",
    "book.nfo",
    "book.cue",
    "book.opf",
    "a.nfo",
    "b.cue",
    "c.nfo",
    "d.cue",
)


def _padded(name: str) -> str:
    if name.endswith(".json"):
        return _fill(_BOM + '{"x": [', '{"a": 1}, ', "{}]}")
    if name.endswith(".opf"):
        return _fill(_BOM + "<package>", '<meta name="calibre:series" ')
    if name.endswith(".cue"):
        return _fill(_BOM + 'TITLE "T"\n', 'REM NARRATOR "N"\n')
    return _fill(_BOM + "Title: T\n", "Narrator: N\n")


def test_eight_marked_documents_in_one_item_read_twice_in_time(
    media_root: Path,
) -> None:
    """Control 2: 8 marked 1 MiB documents, each read twice, still succeed."""
    started = time.monotonic()
    result = _inspect_item(media_root, {name: _padded(name) for name in _EIGHT})
    elapsed = time.monotonic() - started

    assert sorted(value.name for value in result.audio.sidecars) == sorted(_EIGHT)
    assert all(value.byte_order_mark is not None for value in result.audio.sidecars)
    assert elapsed < 15, elapsed


def test_eight_marked_astral_documents_fit_the_childs_address_space(
    media_root: Path,
) -> None:
    """Control 2: each decoded string is 4 bytes a character, read twice."""
    documents = {
        name: _fill(_BOM + '{"title": "\U0001d11e", "x": [', "{}, ", "{}]}")
        if name.endswith(".json")
        else _fill(_BOM + "\U0001d11e\n", "Narrator: N\n")
        for name in _EIGHT
    }

    result = _inspect_item(media_root, documents)

    assert ("asin", "B0SYNTH001", "embedded_tag") in {
        (value.kind.value, value.observed, value.basis) for value in result.claims
    }
    assert all(value.byte_order_mark is not None for value in result.audio.sidecars)
