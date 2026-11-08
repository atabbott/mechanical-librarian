"""The parent inspector's sidecar path, end to end except for ffprobe (#1481).

A stand-in child reads each descriptor it is handed with the real child's
decoder and interprets it with the real grammar over a recorded probe
document, so what is under test is the parent: which documents it opens, which
it hands to which child, how it associates them, and what the manifest and the
claims say.  Every title, name and identifier here is synthetic.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.evidence_extractors import isolated_audio
from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    TargetFile,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioSidecarRefusal,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
)
from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.support.audio_grammar_child import GrammarChild

_TITLE = "The Synthetic Voyage"
_AUTHOR = "Example Author"
_NARRATOR = "Example Narrator"
_ASIN = "B0SYNTH001"
_OTHER_ASIN = "B0SYNTH002"
_MARKER = "Zqx Marker Title Never Published"
_MAXIMUM = 268_435_456


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch) -> Iterator[GrammarChild]:
    stand_in = GrammarChild()
    monkeypatch.setattr(isolated_audio, "run_audio_inspector_child", stand_in)
    yield stand_in


def _context(
    own: tuple[str, ...] | None, *, asin: str | None = None
) -> AudioAssociationContext:
    fields = [
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (_AUTHOR,)),
        TargetField(TargetDestination.NARRATORS, TargetFieldState.VALUE, (_NARRATOR,)),
    ]
    if asin is not None:
        fields.append(TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, asin))
    return AudioAssociationContext(_TITLE, tuple(fields), own)


def _inspect(
    root: Path,
    documents: dict[str, str],
    *,
    tracks: tuple[str, ...] = ("book.m4b",),
    own: tuple[str, ...] | None = None,
    asin: str | None = None,
) -> AudioInspectionResult:
    folder = root / "Item"
    folder.mkdir()
    for name in tracks:
        (folder / name).write_bytes(f"audio {name}".encode())
    for name, text in documents.items():
        (folder / name).write_text(text, encoding="utf-8")
    return IsolatedAudioInspector().inspect(
        MediaMountConfiguration("audiobooks", str(root), "/library", True),
        tuple(
            TargetFile(f"{index:064x}", f"Item/{name}", "m4b")
            for index, name in enumerate(tracks, 1)
        ),
        _MAXIMUM,
        association=_context((*tracks, *documents) if own is None else own, asin=asin),
    )


def _opf(*, title: str = _TITLE, asin: str = _OTHER_ASIN) -> str:
    return (
        "<package><metadata>"
        f"<dc:title>{title}</dc:title>"
        f"<dc:creator>{_AUTHOR}</dc:creator>"
        f'<meta name="calibre:asin" content="{asin}"/>'
        "<dc:language>English</dc:language>"
        "</metadata></package>"
    )


def _libation(asin: str = _OTHER_ASIN) -> str:
    return json.dumps({"asin": asin, "title": _TITLE, "authors": [{"name": _AUTHOR}]})


def _sidecar_claims(result: AudioInspectionResult) -> list[tuple[str, str]]:
    return [
        (claim.kind.value, str(claim.canonical))
        for claim in result.claims
        if claim.basis == "sidecar"
    ]


def _manifest(result: AudioInspectionResult) -> list[tuple[str, str, str, tuple]]:
    return [
        (
            entry.name,
            entry.kind.value,
            entry.binding.value,
            tuple(value.value for value in entry.refusals),
        )
        for entry in result.audio.sidecars
    ]


@pytest.mark.parametrize("name", ("metadata.json", "metadata.abs"))
def test_the_targets_own_record_is_digested_and_never_handed_to_a_child(
    tmp_path: Path, child: GrammarChild, name: str
) -> None:
    """T1, T2, S12: present in the manifest, refused, no claim from it."""
    record = json.dumps(
        {"asin": _ASIN, "title": _TITLE, "series": ["Synthetic Series #3"]}
    )
    result = _inspect(tmp_path, {name: record})

    assert child.handed == [()]
    assert _manifest(result) == [
        (name, "audiobookshelf_record", "folder", ("target_record",))
    ]
    assert _sidecar_claims(result) == []
    assert all(_ASIN != claim.canonical for claim in result.claims)


def test_a_per_track_json_goes_to_its_track_and_carries_its_provenance(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T4: track 2's Libation export reaches track 2's child alone."""
    result = _inspect(
        tmp_path,
        {"02.metadata.json": _libation(), "other.metadata.json": _libation(_ASIN)},
        tracks=("01.m4b", "02.m4b"),
    )

    assert child.handed == [(), ("02.metadata.json",)]
    assert _manifest(result) == [("02.metadata.json", "libation", "track", ())]
    [asin] = [claim for claim in result.claims if claim.kind.value == "asin"]
    assert asin.basis == "sidecar"
    assert asin.canonical == _OTHER_ASIN
    assert [asin.reference in track.claims for track in result.audio.tracks] == [
        False,
        True,
    ]
    assert result.audio.sidecars[0].claims == (asin.reference,)


def test_a_folder_level_document_beside_another_items_file_reaches_no_child(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T5: the shared folder's document is digested but never read."""
    result = _inspect(
        tmp_path,
        {"folder.opf": _opf(), "other.m4b": "another item"},
        own=("book.m4b", "folder.opf"),
    )
    assert child.handed == [()]
    assert _manifest(result) == [("folder.opf", "opf", "folder", ("shared_folder",))]
    assert _sidecar_claims(result) == []


def test_an_associated_document_supplies_claims_the_manifest_cites(
    tmp_path: Path, child: GrammarChild
) -> None:
    result = _inspect(tmp_path, {"folder.opf": _opf()})

    assert child.handed == [("folder.opf",)]
    assert sorted(_sidecar_claims(result)) == [
        ("asin", _OTHER_ASIN),
        ("language", "English"),
    ]
    [entry] = result.audio.sidecars
    assert entry.refusals == ()
    assert set(entry.claims) == {
        claim.reference for claim in result.claims if claim.basis == "sidecar"
    }


def test_another_books_document_supplies_nothing(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T9 and S10: its ASIN is no claim, and its title is never published."""
    result = _inspect(tmp_path, {"folder.opf": _opf(title=_MARKER)})

    assert _manifest(result) == [("folder.opf", "opf", "folder", ("other_book",))]
    assert _sidecar_claims(result) == []
    assert _MARKER not in json.dumps(result.audio.to_data())
    assert all(_MARKER not in str(claim.observed) for claim in result.claims)


def test_an_opf_asin_equal_to_the_asserted_one_is_withheld_as_an_echo(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T16a at the inspector: no `sidecar` ASIN claim, and `echoed` says so."""
    result = _inspect(tmp_path, {"folder.opf": _opf(asin=_ASIN)}, asin=_ASIN)

    assert ("asin", _ASIN) not in _sidecar_claims(result)
    [entry] = result.audio.sidecars
    assert [value.value for value in entry.echoed] == ["asin"]
    assert entry.refusals == ()


def test_the_items_own_cue_is_not_crowded_out_by_foreign_documents(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T24b: the track-bound CUE opens first and is associated."""
    documents = {
        "zz-book.cue": (
            f'TITLE "{_TITLE}"\nPERFORMER "{_AUTHOR}"\nFILE "zz-book.m4b" MP4\n'
            "REM LANGUAGE English\n"
        ),
        **{f"a{index}.opf": _opf(title=f"Unrelated {index}") for index in range(9)},
    }
    result = _inspect(tmp_path, documents, tracks=("zz-book.m4b",))

    assert len(result.audio.sidecars) == 8
    [first] = [value for value in result.audio.sidecars if value.name.endswith(".cue")]
    assert (first.name, first.binding.value, first.refusals) == (
        "zz-book.cue",
        "track",
        (),
    )
    assert first.claims


def test_no_sidecar_name_or_text_reaches_a_log_or_stderr(
    tmp_path: Path,
    child: GrammarChild,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S11: hostile documents leave only closed codes, in the manifest."""
    caplog.set_level(logging.DEBUG)
    documents = {
        "metadata.json": json.dumps({"title": _MARKER}),
        "zz-marker-name.opf": _opf(title=_MARKER),
        "book.cue": f'TITLE "{_MARKER}"\nFILE "{_MARKER}.m4b" MP4\n',
        "book.nfo": "Title: ‮" + _MARKER + "\n",
    }
    result = _inspect(tmp_path, documents)

    captured = capsys.readouterr()
    for text in (captured.out, captured.err, caplog.text):
        assert _MARKER not in text
        assert "zz-marker-name" not in text
    assert all(
        type(value) is AudioSidecarRefusal
        for entry in result.audio.sidecars
        for value in entry.refusals
    )
    assert _MARKER not in json.dumps(result.audio.to_data())


def test_nothing_a_document_references_is_ever_opened(
    tmp_path: Path, child: GrammarChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4: only listed names are opened; a CUE naming `../x.m4b` is `other_file`."""
    opened: list[str] = []
    real = isolated_audio.open_local_media

    def spy(root: str, relative: str):
        opened.append(relative)
        return real(root, relative)

    monkeypatch.setattr(isolated_audio, "open_local_media", spy)
    documents = {
        "book.cue": f'TITLE "{_TITLE}"\nPERFORMER "{_AUTHOR}"\nFILE "../x.m4b" MP4\n',
        "folder.opf": (
            "<package><metadata>"
            f"<dc:title>{_TITLE}</dc:title><dc:creator>{_AUTHOR}</dc:creator>"
            '<item href="../../etc/passwd"/>'
            "</metadata></package>"
        ),
    }
    result = _inspect(tmp_path, documents)

    assert sorted(opened) == ["Item/book.cue", "Item/book.m4b", "Item/folder.opf"]
    assert _manifest(result) == [
        ("book.cue", "cue", "track", ("other_file",)),
        ("folder.opf", "opf", "folder", ()),
    ]


def test_unchanged_media_stays_current_whatever_order_the_documents_open_in(
    tmp_path: Path, child: GrammarChild
) -> None:
    """Codex r5: documents open track-bound, then record, then folder.

    The manifest is sorted by name, so `aaa.nfo`, `book.opf` and the target's
    `metadata.json` open in another order than the manifest lists them.  The
    comparison is by name, so unchanged media stays current.
    """
    documents = {
        "aaa.nfo": f"Title: {_TITLE}\nAuthor: {_AUTHOR}\n",
        "book.metadata.json": _libation(),
        "book.opf": _opf(),
        "metadata.json": json.dumps({"title": _TITLE}),
    }
    result = _inspect(tmp_path, documents)
    assert [entry.name for entry in result.audio.sidecars] == sorted(documents)

    def current() -> bool:
        return IsolatedAudioInspector().unchanged(
            MediaMountConfiguration("audiobooks", str(tmp_path), "/library", True),
            ("Item/book.m4b",),
            result.content_digest,
            _MAXIMUM,
            result.audio.sidecars,
            result.audio.tracks,
            own_file_names=("book.m4b", *documents),
        )

    assert current()
    (tmp_path / "Item" / "aaa.nfo").write_text("Title: Changed\n", encoding="utf-8")
    assert not current()
