"""A byte-order mark never changes what an audiobook's report believes (#1796).

T5 and controls 5 and 6.  Over generated items -- zero to eight documents of
every kind, some opening with U+FEFF and holding on line 1 a title, creator,
ASIN, narrator or CUE `FILE` -- the real inspector's whole result is compared
with the same inspector given a child whose answers carry no reading without
the mark: the claims, the content digest, the audio section less each entry's
`byte_order_mark`, and the item's outcome must be identical.  Cases are built
to make the shadow pass differ: values that would dispute or echo another
document's, a hidden title or creator that would make a refused document
belong, a hidden CUE `FILE` naming another track or none, a hidden narrator
equal to the target's, narrator-only documents, and documents whose facts
would pass 256 item-wide.  A second test proves the cases are not vacuous.

The reader inventory below is the prompt: `.stripped` and the record are read
only where the contract lists them.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import random
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mechanical_librarian.adapters.evidence_extractors import isolated_audio
from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    AudioItemInspectionError,
    TargetFile,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioInspection,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
)
from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
    TargetField,
    TargetFieldState,
    canonical_json,
)
from tests.support.audio_grammar_child import GrammarChild

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mechanical_librarian"
SCRIPTS_ROOT = Path(__file__).parents[2] / "scripts"
_BOM = "﻿"
_TITLE = "The Synthetic Voyage"
_AUTHOR = "Example Author"
_NARRATOR = "Example Narrator"
# Weighted toward the target, so most documents belong and their hidden first
# line has something to change.
_TITLES = (_TITLE, _TITLE, _TITLE, "A Different Synthetic Book")
_CREATORS = (_AUTHOR, _AUTHOR, _AUTHOR, "Another Synthetic Author")
_NARRATORS = (_NARRATOR, "Second Narrator", "Third Voice")
_ASINS = ("B0SYNTH001", "B0SYNTH002")
_TRACKS = ("book.m4b", "part2.m4b")
_NAMES = (
    "book.nfo",
    "book.cue",
    "book.opf",
    "book.metadata.json",
    "folder.nfo",
    "folder.cue",
    "folder.opf",
    "notes.nfo",
)


class _Child(GrammarChild):
    """The grammar child through the protocol; `blind` drops the second reading."""

    def __init__(self, *, blind: bool) -> None:
        super().__init__()
        self.blind = blind

    def __call__(self, *arguments, **keywords) -> AudioInspection:
        answer = super().__call__(*arguments, **keywords)
        if self.blind:
            answer = dataclasses.replace(
                answer,
                sidecars=tuple(
                    dataclasses.replace(
                        value, byte_order_mark=False, stripped=None, stripped_limit=None
                    )
                    for value in answer.sidecars
                ),
            )
        return AudioInspection.from_json(answer.to_json_within(2_097_152))


def _line_one(rng: random.Random, kind: str) -> str:
    choice = rng.choice(
        ("title", "creator", "asin", "asin", "narrator", "narrator", "file", "none")
    )
    if choice == "title":
        value = rng.choice(_TITLES)
        return f'TITLE "{value}"' if kind == "cue" else f"Title: {value}"
    if choice == "creator":
        value = rng.choice(_CREATORS)
        return f'PERFORMER "{value}"' if kind == "cue" else f"Author: {value}"
    if choice == "asin":
        value = rng.choice(_ASINS)
        return f"REM ASIN {value}" if kind == "cue" else f"ASIN: {value}"
    if choice == "narrator":
        value = rng.choice(_NARRATORS)
        return f"REM NARRATOR {value}" if kind == "cue" else f"Narrator: {value}"
    if choice == "file" and kind == "cue":
        return f'FILE "{rng.choice((*_TRACKS, "elsewhere.m4b", "../x.m4b"))}" MP4'
    return "REM COMMENT none" if kind == "cue" else "Comment: none"


def _line_document(rng: random.Random, kind: str) -> str:
    lines = [_line_one(rng, kind)]
    narrator_only = rng.random() < 0.2
    many = rng.random() < 0.08
    if kind == "cue":
        if not narrator_only:
            if rng.random() < 0.85:
                lines.append(f'TITLE "{rng.choice(_TITLES)}"')
            if rng.random() < 0.85:
                lines.append(f'PERFORMER "{rng.choice(_CREATORS)}"')
            if rng.random() < 0.5:
                lines.append(f'FILE "{rng.choice(_TRACKS)}" MP4')
        lines.append(f"REM NARRATOR {rng.choice(_NARRATORS)}")
        if rng.random() < 0.5:
            lines.append(f"REM ASIN {rng.choice(_ASINS)}")
    else:
        if not narrator_only:
            if rng.random() < 0.85:
                lines.append(f"Title: {rng.choice(_TITLES)}")
            if rng.random() < 0.85:
                lines.append(f"Author: {rng.choice(_CREATORS)}")
        lines.append(f"Narrator: {rng.choice(_NARRATORS)}")
        if rng.random() < 0.5:
            lines.append(f"ASIN: {rng.choice(_ASINS)}")
        if rng.random() < 0.3:
            lines.append(f"Language: {rng.choice(('English', 'German'))}")
    if many:
        lines.extend(f"Narrator: Crowd Voice {index:03d}" for index in range(300))
    return "\n".join(lines) + "\n"


def _opf(rng: random.Random) -> str:
    parts = ["<package><metadata>"]
    if rng.random() < 0.8:
        parts.append(f"<dc:title>{rng.choice(_TITLES)}</dc:title>")
    if rng.random() < 0.8:
        parts.append(f"<dc:creator>{rng.choice(_CREATORS)}</dc:creator>")
    parts.append(
        f'<dc:contributor opf:role="nrt">{rng.choice(_NARRATORS)}</dc:contributor>'
    )
    parts.append(f'<meta name="calibre:asin" content="{rng.choice(_ASINS)}"/>')
    parts.append("</metadata></package>")
    return "".join(parts)


def _libation(rng: random.Random) -> str:
    data: dict[str, object] = {"asin": rng.choice(_ASINS)}
    if rng.random() < 0.8:
        data["title"] = rng.choice(_TITLES)
    if rng.random() < 0.8:
        data["authors"] = [{"name": rng.choice(_CREATORS)}]
    data["narrators"] = [{"name": rng.choice(_NARRATORS)}]
    return json.dumps(data)


def _scenario(rng: random.Random) -> tuple[tuple[str, ...], dict[str, str]]:
    tracks = _TRACKS[: rng.choice((1, 1, 2))]
    documents: dict[str, str] = {}
    for name in rng.sample(_NAMES, rng.randint(0, 8)):
        if name.endswith(".metadata.json"):
            text = _libation(rng)
        elif name.endswith(".opf"):
            text = _opf(rng)
        else:
            text = _line_document(rng, name.rsplit(".", 1)[1])
        documents[name] = (_BOM if rng.random() < 0.6 else "") + text
    return tracks, documents


def _without_records(audio: dict[str, object]) -> str:
    sidecars = [
        {key: value for key, value in entry.items() if key != "byte_order_mark"}
        for entry in audio["sidecars"]
    ]
    return canonical_json({**audio, "sidecars": sidecars})


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tracks: tuple[str, ...],
    documents: dict[str, str],
    *,
    blind: bool,
) -> AudioInspectionResult | str:
    monkeypatch.setattr(
        isolated_audio, "run_audio_inspector_child", _Child(blind=blind)
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "Item").mkdir()
        for track in tracks:
            (root / "Item" / track).write_bytes(f"audio {track}".encode())
        for name, text in documents.items():
            (root / "Item" / name).write_text(text, encoding="utf-8")
        fields = (
            TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (_AUTHOR,)),
            TargetField(
                TargetDestination.NARRATORS, TargetFieldState.VALUE, (_NARRATOR,)
            ),
            TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, _ASINS[0]),
        )
        try:
            return IsolatedAudioInspector().inspect(
                MediaMountConfiguration("audiobooks", str(root), "/library", True),
                tuple(
                    TargetFile(f"{index:064x}", f"Item/{track}", "m4b")
                    for index, track in enumerate(tracks, 1)
                ),
                268_435_456,
                association=AudioAssociationContext(
                    _TITLE, fields, (*tracks, *documents)
                ),
            )
        except AudioItemInspectionError as error:
            return error.outcome.value


def _differs(result: AudioInspectionResult) -> bool:
    """Whether the shadow pass decided anything the claimed pass did not."""
    return any(
        entry.byte_order_mark is not None
        and (
            entry.byte_order_mark.facts
            or entry.byte_order_mark.refusals != entry.refusals
        )
        for entry in result.audio.sidecars
    )


def _assert_shadow_only(
    monkeypatch: pytest.MonkeyPatch, rng: random.Random
) -> AudioInspectionResult | str:
    tracks, documents = _scenario(rng)
    marked = _run(monkeypatch, tracks, documents, blind=False)
    oracle = _run(monkeypatch, tracks, documents, blind=True)
    if isinstance(marked, str) or isinstance(oracle, str):
        assert marked == oracle
        return marked
    assert marked.claims == oracle.claims
    assert marked.content_digest == oracle.content_digest
    assert _without_records(marked.audio.to_data()) == _without_records(
        oracle.audio.to_data()
    )
    return marked


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.randoms(use_true_random=False))
def test_a_byte_order_mark_never_changes_the_inspection_result(
    monkeypatch: pytest.MonkeyPatch, rng: random.Random
) -> None:
    _assert_shadow_only(monkeypatch, rng)


def test_the_generated_items_make_the_shadow_pass_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity: a fixed share of seeded items have a differing shadow pass."""
    results = [
        _assert_shadow_only(monkeypatch, random.Random(seed)) for seed in range(80)
    ]
    differing = sum(
        1 for value in results if not isinstance(value, str) and _differs(value)
    )
    assert differing >= 12, differing


# --- Reader inventory (control 5) ---------------------------------------------

# `.stripped` is produced by the child adapter, validated and serialized by the
# protocol, and read by exactly one builder in the parent.
_STRIPPED_READERS = {
    "adapters/evidence_extractors/audio_local.py",
    "domain/audio_inspection.py",
    "adapters/evidence_extractors/isolated_audio.py::_documents",
}
# The record is built in the parent, parsed and validated by the report, and
# counted by the cold-start script.
_RECORD_READERS = {
    "domain/audio_inspection.py",
    "domain/metadata_report.py::_audio_sidecar_from_data",
    "domain/metadata_report.py::AudioFacts.__post_init__",
    "domain/metadata_report.py::audio_record_rungs",
    "adapters/evidence_extractors/isolated_audio.py::_sidecar_manifest",
    "adapters/evidence_extractors/isolated_audio.py::_byte_order_mark_record",
    "adapters/evidence_extractors/isolated_audio.py::_capped_records",
    "scripts/cold_start_report.py::_byte_order_mark_entries",
}


def _attribute_readers(attribute: str) -> set[str]:
    found: set[str] = set()
    for root, prefix in ((PACKAGE_ROOT, ""), (SCRIPTS_ROOT, "scripts/")):
        for path in sorted(root.rglob("*.py")):
            relative = prefix + path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for owner, node in _walk(tree, ()):
                if isinstance(node, ast.Attribute) and node.attr == attribute:
                    found.add(f"{relative}::{owner}" if owner else relative)
    return found


def _walk(node: ast.AST, scope: tuple[str, ...]):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield from _walk(child, (*scope, child.name))
        else:
            yield ".".join(scope), child
            yield from _walk(child, scope)


def _within(found: set[str], allowed: set[str]) -> set[str]:
    return {
        value
        for value in found
        if value not in allowed and value.split("::", 1)[0] not in allowed
    }


def test_the_reading_without_the_mark_is_read_only_where_listed() -> None:
    assert _within(_attribute_readers("stripped"), _STRIPPED_READERS) == set()


def test_the_record_is_read_only_where_listed() -> None:
    found = _attribute_readers("byte_order_mark")
    assert _within(found, _RECORD_READERS) == set()
    assert not any(value.startswith("application/") for value in found)
    assert not any(
        owner in value
        for value in found
        for owner in ("_parent_view", "unchanged", "associate_sidecars", "planning")
    )
