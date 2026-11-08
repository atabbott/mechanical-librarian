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
from mechanical_librarian.application.metadata_report import TargetFile
from mechanical_librarian.domain.audio_inspection import AudioInspectionLimits
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
)
from tests.support.audio_containers import Mp4Fixture, mp4_audiobook

_LIMIT = AudioInspectionLimits().sidecar_bytes


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


@pytest.mark.parametrize(
    ("name", "document"),
    _HOSTILE,
    ids=(
        "L1_meta",
        "L2_element",
        "L3_attribute",
        "L4_nfo_line",
        "L5_cue_line",
        "L6_sequence",
        "L7_asin",
    ),
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
