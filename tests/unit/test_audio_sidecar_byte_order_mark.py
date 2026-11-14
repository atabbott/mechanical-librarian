"""Sidecars that start with a byte-order mark are also read without it (#1796).

The child reads such a document twice: as it is, which is the only reading
that can become a claim, and once without one leading U+FEFF.  What only the
second reading finds is recorded in the document's manifest entry and never
believed.  These cases cover the child's two readings and its budgets, the
child protocol, the manifest record and the size steps that keep a record from
denying an item or its report.  Every title, name and identifier is synthetic.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.evidence_extractors import (
    audio_local,
    isolated_audio,
)
from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    AudioInterpretation,
    AudioPathContext,
    AudioSidecar,
    interpret,
)
from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    AudioItemInspectionError,
    TargetFile,
    TargetSnapshot,
    audiobook_discovery_plan,
)
from mechanical_librarian.bootstrap import audio_child
from mechanical_librarian.domain import metadata_report
from mechanical_librarian.domain.audio_inspection import (
    MAX_BYTE_ORDER_MARK_BYTES,
    MAX_FACTS,
    AudioChapter,
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionFact,
    AudioInspectionField,
    AudioInspectionLimits,
    AudioInspectionOutcome,
    AudioSidecarBinding,
    AudioSidecarByteOrderMark,
    AudioSidecarByteOrderMarkFact,
    AudioSidecarDigest,
    AudioSidecarKind,
    AudioSidecarReading,
    AudioSidecarRefusal,
    AudioSidecarStrippedLimit,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
    SidecarOutcome,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    _audio_sidecar_from_data,
    audio_record_rungs,
    canonical_json,
    default_target_fields,
    echoed_target_values,
    normalize_title,
)
from tests.support.audio_grammar_child import GrammarChild
from tests.support.metadata_report_doubles import single_track_audio_facts

_SOURCE = Fingerprint("sha256", SchemaVersion("local-file-v1"), "1" * 64)
_BOM = "﻿"
_TITLE = "The Synthetic Voyage"
_AUTHOR = "Example Author"
_NARRATOR = "Example Narrator"
_ASIN = "B0SYNTH001"
_OTHER_ASIN = "B0SYNTH002"
_MARKER = "Zqx Marker Title Never Published"
_TRACK = "book.m4b"
_MAXIMUM = 268_435_456
_F = AudioInspectionField
_R = AudioSidecarRefusal
_LIBATION = AudioEvidenceSurface.SIDECAR_LIBATION
_OPF = AudioEvidenceSurface.SIDECAR_OPF
_NFO = AudioEvidenceSurface.SIDECAR_NFO
_CUE = AudioEvidenceSurface.SIDECAR_CUE


def _interpret(*sidecars: AudioSidecar) -> AudioInterpretation:
    probe = json.dumps(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "4.000"},
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "chapters": [],
        }
    )
    return interpret(
        probe, sidecars, AudioPathContext(_TRACK), _SOURCE, AudioInspectionLimits()
    )


def _read(surface: AudioEvidenceSurface, name: str, text: str) -> AudioSidecarReading:
    [reading] = _interpret(AudioSidecar(surface, name, text)).sidecars
    return reading


def _libation(asin: str = _ASIN, **extra: object) -> str:
    return json.dumps(
        {
            "asin": asin,
            "title": _TITLE,
            "authors": [{"name": _AUTHOR}],
            "narrators": [{"name": _NARRATOR}],
            **extra,
        }
    )


def _values(reading: AudioSidecarReading) -> set[tuple[str, str | None]]:
    return {(fact.field.value, fact.canonical_value) for fact in reading.facts}


# --- The child's two readings (S1 to S3, control 1) ---------------------------


def test_a_libation_export_with_a_mark_is_also_read_without_it() -> None:
    """T1, replacing #1481's guard: the plain reading is today's, empty."""
    reading = _read(_LIBATION, "book.metadata.json", _BOM + _libation())

    assert reading.byte_order_mark is True
    assert (reading.facts, reading.title, reading.creators) == ((), None, ())
    assert reading.stripped is not None
    assert ("asin", _ASIN) in _values(reading.stripped)
    assert reading.stripped.title == _TITLE
    assert reading.stripped.creators[0] == _AUTHOR
    assert reading.stripped.byte_order_mark is False
    assert reading.stripped.stripped is None
    assert reading.stripped == _read(_LIBATION, "book.metadata.json", _libation())


@pytest.mark.parametrize(
    ("surface", "name", "text", "recovered"),
    (
        (_NFO, "book.nfo", f"Title: {_TITLE}\nAuthor: {_AUTHOR}\n", "title"),
        (
            _NFO,
            "book.nfo",
            f"ASIN: {_ASIN}\nTitle: {_TITLE}\nAuthor: {_AUTHOR}\n",
            "asin",
        ),
        (
            _CUE,
            "book.cue",
            f'FILE "{_TRACK}" MP4\nTITLE "{_TITLE}"\nPERFORMER "{_AUTHOR}"\n',
            "file",
        ),
        (
            _CUE,
            "book.cue",
            f'TITLE "{_TITLE}"\nPERFORMER "{_AUTHOR}"\nFILE "{_TRACK}" MP4\n',
            "title",
        ),
    ),
    ids=("nfo_title", "nfo_asin", "cue_file", "cue_title"),
)
def test_a_line_sidecar_without_its_mark_recovers_exactly_line_one(
    surface: AudioEvidenceSurface, name: str, text: str, recovered: str
) -> None:
    """T3: the plain reading is today's, which cannot read line 1."""
    reading = _read(surface, name, _BOM + text)
    without_line_one = _read(surface, name, text.split("\n", 1)[1])

    assert (
        dataclasses.replace(reading, byte_order_mark=False, stripped=None)
        == without_line_one
    )
    assert reading.stripped == _read(surface, name, text)
    stripped = reading.stripped
    assert stripped is not None
    if recovered == "title":
        assert (reading.title, stripped.title) == (None, _TITLE)
    elif recovered == "asin":
        assert ("asin", _ASIN) in _values(stripped) - _values(reading)
    else:
        assert (reading.file_reference, stripped.file_reference) == (None, _TRACK)


def test_an_opf_with_a_mark_is_scanned_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control 1: OPF is skipped by kind, never read twice and compared."""
    calls: list[str] = []
    real = audio_local._opf_sidecar_facts

    def spy(*arguments: object) -> None:
        calls.append("opf")
        real(*arguments)  # type: ignore[arg-type]

    monkeypatch.setattr(audio_local, "_opf_sidecar_facts", spy)
    text = (
        "<package><metadata>"
        f"<dc:title>{_TITLE}</dc:title><dc:creator>{_AUTHOR}</dc:creator>"
        f'<meta name="calibre:asin" content="{_ASIN}"/>'
        "</metadata></package>"
    )
    reading = _read(_OPF, "book.opf", _BOM + text)

    assert calls == ["opf"]
    assert reading.byte_order_mark is True
    assert reading.stripped is None
    assert reading.stripped_limit is None


@pytest.mark.parametrize(
    ("text", "flagged"),
    (
        (_BOM + _BOM + f"Title: {_TITLE}\nNarrator: {_NARRATOR}\n", True),
        (_BOM, True),
        (f"Narrator: {_NARRATOR}\n{_BOM}ASIN: {_ASIN}\n", False),
        (f"￾Title: {_TITLE}\nNarrator: {_NARRATOR}\n", False),
        (f"​Title: {_TITLE}\nNarrator: {_NARRATOR}\n", False),
        (f"⁠Title: {_TITLE}\nNarrator: {_NARRATOR}\n", False),
    ),
    ids=(
        "bom_bom",
        "bom_only",
        "mid_file",
        "fffe_leading",
        "zwsp_leading",
        "word_joiner_leading",
    ),
)
def test_only_one_leading_mark_is_ever_removed(text: str, flagged: bool) -> None:
    """T4, control 1: no loop, no look-alike, no mid-file strip."""
    reading = _read(_NFO, "book.nfo", text)

    assert reading.byte_order_mark is flagged
    # A doubled mark still opens the text read without one, so that reading
    # equals the plain one and is not kept; nothing else is a mark at all, and
    # a line opening with one is unreadable as on 905cc06.
    assert reading.stripped is None
    assert reading.stripped_limit is None
    assert reading.title is None
    if text == _BOM:
        assert (reading.facts, reading.creators) == ((), ())
    if text.startswith("Narrator"):
        assert _values(reading) == {("contributor", _NARRATOR)}


def _narrators(prefix: str, count: int) -> str:
    return "".join(f"Narrator: {prefix} Voice {index:03d}\n" for index in range(count))


def test_a_mark_never_costs_a_later_document_a_fact() -> None:
    """Control 2, T9: plain readings first, under today's budget and order."""
    marked = AudioSidecar(_NFO, "a.nfo", f"{_BOM}ASIN: {_ASIN}\n" + _narrators("A", 10))
    later = AudioSidecar(_NFO, "b.nfo", _narrators("B", 300))
    unmarked = AudioSidecar(_NFO, "a.nfo", "Comment: none\n" + _narrators("A", 10))

    first, second = _interpret(marked, later).sidecars
    _, baseline = _interpret(unmarked, later).sidecars

    file_facts = len(_interpret().facts)
    assert len(first.facts) == 10
    assert second.facts == baseline.facts
    assert len(first.facts) + len(second.facts) == MAX_FACTS - file_facts
    # Nothing is left for the reading without the mark, and it says so.
    assert first.stripped is not None
    assert first.stripped.facts == ()
    assert first.stripped_limit is AudioSidecarStrippedLimit.FACT_BUDGET


def test_a_reading_without_its_mark_draws_on_what_the_plain_readings_left() -> None:
    marked = AudioSidecar(_NFO, "a.nfo", f"{_BOM}ASIN: {_ASIN}\n" + _narrators("A", 2))
    later = AudioSidecar(_NFO, "b.nfo", _narrators("B", 3))

    first, second = _interpret(marked, later).sidecars

    assert len(second.facts) == 3
    assert first.stripped is not None
    assert ("asin", _ASIN) in _values(first.stripped)
    assert first.stripped_limit is None


def test_a_budget_cut_with_nothing_else_recovered_still_records_the_cut() -> None:
    """Root addition: no facts left is never read as nothing extra found."""
    marked = AudioSidecar(_NFO, "a.nfo", f"{_BOM}ASIN: {_ASIN}\n")
    later = AudioSidecar(_NFO, "b.nfo", _narrators("B", 300))

    first, _ = _interpret(marked, later).sidecars

    assert first.stripped is None
    assert first.stripped_limit is AudioSidecarStrippedLimit.FACT_BUDGET


# --- Child protocol v6 (S5, control 3) ---------------------------------------


def _fact(
    field: AudioInspectionField,
    value: str,
    surface: AudioEvidenceSurface = _NFO,
) -> AudioInspectionFact:
    return AudioInspectionFact(
        field,
        _SOURCE,
        value,
        value,
        value,
        surface,
        role="narrator" if field is _F.CONTRIBUTOR else None,
    )


def _marked_reading(
    surface: AudioEvidenceSurface = _NFO, name: str = "book.nfo"
) -> AudioSidecarReading:
    return AudioSidecarReading(
        name,
        surface,
        (_fact(_F.CONTRIBUTOR, _NARRATOR, surface),),
        creators=(_AUTHOR,),
        byte_order_mark=True,
        stripped=AudioSidecarReading(
            name,
            surface,
            (_fact(_F.CONTRIBUTOR, _NARRATOR, surface), _fact(_F.ASIN, _ASIN, surface)),
            _TITLE,
            (_AUTHOR,),
        ),
    )


def _answer(
    *readings: AudioSidecarReading, chapters: tuple[AudioChapter, ...] = ()
) -> AudioInspection:
    return AudioInspection(
        AudioInspectionOutcome.SUCCESS, _SOURCE, chapters=chapters, sidecars=readings
    )


def test_protocol_v6_round_trips_a_reading_without_its_mark() -> None:
    report = _answer(_marked_reading())
    serialized = report.to_json()

    assert AudioInspection.from_json(serialized) == report
    assert repr(report.sidecars[0].stripped) == "AudioSidecarReading(<redacted>)"
    old = json.loads(serialized)
    old["schema"] = "audio-inspection-v5"
    with pytest.raises(ValueError, match="protocol"):
        AudioInspection.from_json(json.dumps(old))


def _edited(edit) -> str:
    data = json.loads(_answer(_marked_reading()).to_json())
    edit(data["sidecars"][0])
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _nest(reading: dict[str, object]) -> None:
    inner = dict(cast("dict[str, Any]", reading["stripped"]))
    inner["byte_order_mark"] = True
    inner["stripped"] = dict(inner, byte_order_mark=False, stripped=None)
    inner["stripped"]["title"] = "Another Synthetic Title"
    reading["stripped"] = inner


def _on_opf(reading: dict[str, object]) -> None:
    for value in (reading, reading["stripped"]):
        value["surface"] = "sidecar_opf"
        for fact in value["facts"]:
            fact["surface"] = "sidecar_opf"


def _equal(reading: dict[str, object]) -> None:
    reading["stripped"] = dict(
        reading, byte_order_mark=False, stripped=None, stripped_limit=None
    )


def _unflagged(reading: dict[str, object]) -> None:
    reading["byte_order_mark"] = False


def _renamed(reading: dict[str, object]) -> None:
    reading["stripped"]["name"] = "other.nfo"


def _resurfaced(reading: dict[str, object]) -> None:
    stripped = reading["stripped"]
    stripped["surface"] = "sidecar_cue"
    for fact in stripped["facts"]:
        fact["surface"] = "sidecar_cue"


def _omitted_but_present(reading: dict[str, object]) -> None:
    reading["stripped_limit"] = "child_output"


def _limit_unflagged(reading: dict[str, object]) -> None:
    reading["stripped"] = None
    reading["byte_order_mark"] = False
    reading["stripped_limit"] = "fact_budget"


def _flagged_inner(reading: dict[str, object]) -> None:
    reading["stripped"]["byte_order_mark"] = True


def _unknown_limit(reading: dict[str, object]) -> None:
    reading["stripped_limit"] = "too_large"


@pytest.mark.parametrize(
    "edit",
    (
        _nest,
        _on_opf,
        _equal,
        _unflagged,
        _renamed,
        _resurfaced,
        _omitted_but_present,
        _limit_unflagged,
        _flagged_inner,
        _unknown_limit,
    ),
    ids=(
        "nested_stripped",
        "stripped_on_opf",
        "stripped_equal_to_plain",
        "stripped_without_flag",
        "stripped_other_name",
        "stripped_other_surface",
        "child_output_with_stripped",
        "limit_without_flag",
        "stripped_carries_flag",
        "unknown_limit",
    ),
)
def test_the_protocol_refuses_a_reading_without_its_mark_out_of_shape(edit) -> None:
    """Control 3: one level, same name and surface, never OPF, never equal."""
    with pytest.raises(ValueError, match="protocol"):
        AudioInspection.from_json(_edited(edit))


def test_plain_and_stripped_facts_share_one_budget() -> None:
    """Control 3: 257 facts across a plain and a stripped reading are refused."""
    plain = AudioSidecarReading(
        "book.nfo",
        _NFO,
        tuple(_fact(_F.CONTRIBUTOR, f"Voice {index:03d}") for index in range(100)),
        byte_order_mark=True,
    )
    stripped = AudioSidecarReading(
        "book.nfo",
        _NFO,
        tuple(_fact(_F.CONTRIBUTOR, f"Other {index:03d}") for index in range(157)),
    )
    with pytest.raises(ValueError):
        _answer(dataclasses.replace(plain, stripped=stripped))
    data = json.loads(_answer(plain).to_json())
    data["sidecars"][0]["stripped"] = stripped.to_data()
    with pytest.raises(ValueError, match="protocol"):
        AudioInspection.from_json(
            json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
    # One fewer fits.
    _answer(
        dataclasses.replace(
            plain, stripped=dataclasses.replace(stripped, facts=stripped.facts[1:])
        )
    )


def test_a_stripped_fact_from_another_source_is_refused() -> None:
    other = Fingerprint("sha256", SchemaVersion("local-file-v1"), "2" * 64)
    reading = _marked_reading()
    assert reading.stripped is not None
    foreign = dataclasses.replace(
        reading,
        stripped=dataclasses.replace(
            reading.stripped,
            facts=tuple(
                dataclasses.replace(fact, source=other)
                for fact in reading.stripped.facts
            ),
        ),
    )
    with pytest.raises(ValueError):
        _answer(foreign)


# --- Child output (control 4) -------------------------------------------------


def _near_limit(reading: AudioSidecarReading) -> AudioInspection:
    return _answer(
        reading,
        chapters=tuple(
            AudioChapter(index + 1, index, index + 1, "\U0001d11e" * 280)
            for index in range(1_024)
        ),
    )


def test_an_answer_too_large_with_its_stripped_readings_drops_them() -> None:
    report = _near_limit(_marked_reading())
    without = dataclasses.replace(
        report,
        sidecars=(
            dataclasses.replace(
                report.sidecars[0],
                stripped=None,
                stripped_limit=AudioSidecarStrippedLimit.CHILD_OUTPUT,
            ),
        ),
    ).to_json()
    maximum = len(without.encode("utf-8"))
    assert len(report.to_json().encode("utf-8")) > maximum

    serialized = report.to_json_within(maximum)

    assert serialized == without
    [reading] = AudioInspection.from_json(serialized).sidecars
    assert reading.byte_order_mark is True
    assert reading.stripped is None
    assert reading.stripped_limit is AudioSidecarStrippedLimit.CHILD_OUTPUT
    assert reading.facts == report.sidecars[0].facts
    # An answer that fits is written as it is.
    assert report.to_json_within(maximum * 2) == report.to_json()


def test_the_child_writes_the_smaller_answer_and_fails_only_past_it(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    report = _near_limit(_marked_reading())
    without = report.to_json_within(0)
    monkeypatch.setattr(
        audio_child, "_STDOUT_LIMIT", len((without + "\n").encode("utf-8"))
    )

    audio_child._write_report(report)

    written = capfd.readouterr().out
    assert written == without + "\n"
    monkeypatch.setattr(audio_child, "_STDOUT_LIMIT", len(without.encode("utf-8")))
    with pytest.raises(ValueError, match="exceeded protocol"):
        audio_child._write_report(report)


# --- The manifest record (S5, controls 7 and 8) ------------------------------


def _record_fact(
    field: str = "asin", observed: str = _ASIN, role: str | None = None
) -> dict[str, object]:
    return {"canonical": observed, "field": field, "observed": observed, "role": role}


def _entry_data(**record: object) -> dict[str, object]:
    return {
        "binding": "folder",
        "byte_order_mark": {
            "facts": [_record_fact()],
            "facts_omitted": False,
            "omitted": None,
            "refusals": [],
            **record,
        },
        "claims": [],
        "digest": "a" * 64,
        "disputed": [],
        "echoed": [],
        "echoed_narrators": [],
        "kind": "nfo",
        "name": "folder.nfo",
        "refusals": ["other_book"],
        "shadow": [],
    }


def test_the_manifest_reads_a_closed_record() -> None:
    entry = _audio_sidecar_from_data(_entry_data())

    assert entry.byte_order_mark == AudioSidecarByteOrderMark(
        facts=(AudioSidecarByteOrderMarkFact(_F.ASIN, _ASIN, _ASIN),)
    )
    assert entry.to_data() == _entry_data()
    assert set(cast("dict[str, object]", entry.to_data()["byte_order_mark"])) == {
        "facts",
        "facts_omitted",
        "omitted",
        "refusals",
    }
    assert set(_record_fact()) == {"canonical", "field", "observed", "role"}
    assert repr(entry.byte_order_mark) == "AudioSidecarByteOrderMark(<redacted>)"


@pytest.mark.parametrize(
    "entry",
    (
        _entry_data(refusals=["not_a_code"]),
        dict(
            _entry_data(),
            kind="audiobookshelf_record",
            name="metadata.json",
            refusals=["target_record"],
        ),
        dict(_entry_data(), refusals=["shared_folder"]),
        _entry_data(refusals=["other_book"]),
        _entry_data(facts=[_record_fact("duration", "4.000")]),
        _entry_data(facts=[_record_fact("asin", _ASIN, "narrator")]),
        _entry_data(facts=[_record_fact("contributor", _NARRATOR)]),
        _entry_data(facts=[dict(_record_fact(), support=_ASIN)]),
        _entry_data(title=_TITLE),
        _entry_data(omitted="child_output"),
        _entry_data(refusals=["other_file"]),
        _entry_data(refusals=["shared_folder"], facts=[]),
        _entry_data(facts=[_record_fact("description", "D" * 513)]),
        _entry_data(facts=[_record_fact("asin", "A" * 281)]),
        _entry_data(facts=[_record_fact("asin", "/home/someone/book")]),
        _entry_data(facts_omitted=1),
    ),
    ids=(
        "unknown_code",
        "record_on_audiobookshelf_record",
        "record_on_parent_refusal",
        "facts_beside_terminal",
        "non_sidecar_field",
        "role_on_non_contributor",
        "contributor_without_role",
        "support_excerpt",
        "association_text_key",
        "child_output_with_facts",
        "other_file_on_nfo",
        "parent_refusal_in_record",
        "description_past_512",
        "observed_past_280",
        "path_value",
        "facts_omitted_not_bool",
    ),
)
def test_the_manifest_refuses_a_record_out_of_shape(entry: dict[str, object]) -> None:
    """T7, control 7."""
    with pytest.raises(ReportValueError):
        _audio_sidecar_from_data(entry)


def test_a_record_fact_takes_the_inspection_facts_own_bounds() -> None:
    """Control 7: a description up to 512 and a canonical up to 512."""
    AudioSidecarByteOrderMarkFact(_F.DESCRIPTION, "D" * 512, "D" * 512)
    AudioSidecarByteOrderMarkFact(_F.CONTRIBUTOR, "N" * 280, "N" * 280, "narrator")
    with pytest.raises(ValueError):
        AudioSidecarByteOrderMarkFact(_F.CONTRIBUTOR, "N" * 281, None, "narrator")


def _records(*counts: int) -> AudioFacts:
    return dataclasses.replace(single_track_audio_facts(), sidecars=_digests(*counts))


def _digests(*counts: int) -> tuple[AudioSidecarDigest, ...]:
    return tuple(
        AudioSidecarDigest(
            f"{index}.nfo",
            "a" * 64,
            AudioSidecarKind.NFO,
            AudioSidecarBinding.FOLDER,
            byte_order_mark=AudioSidecarByteOrderMark(
                facts=tuple(
                    AudioSidecarByteOrderMarkFact(
                        _F.CONTRIBUTOR, f"Voice {fact:03d}", None, "narrator"
                    )
                    for fact in range(count)
                )
            ),
        )
        for index, count in enumerate(counts)
    )


def test_an_audio_section_holds_at_most_256_record_facts() -> None:
    """Control 7: item-wide, refused on the read path too."""
    audio = _records(200, 56)
    assert metadata_report._audio_facts_from_data(audio.to_data()) == audio
    with pytest.raises(ReportValueError):
        _records(200, 57)
    data = audio.to_data()
    data["sidecars"][1]["byte_order_mark"]["facts"].append(
        {
            "canonical": None,
            "field": "contributor",
            "observed": "Extra",
            "role": "narrator",
        }
    )
    with pytest.raises(ReportValueError):
        metadata_report._audio_facts_from_data(data)


def test_the_record_rungs_empty_facts_and_then_remove_records() -> None:
    audio = _records(3, 0)

    full, emptied, removed = audio_record_rungs(audio)

    assert full == audio
    assert [entry.byte_order_mark for entry in emptied.sidecars] == [
        AudioSidecarByteOrderMark(facts_omitted=True),
        AudioSidecarByteOrderMark(),
    ]
    assert [entry.byte_order_mark for entry in removed.sidecars] == [None, None]
    assert audio_record_rungs(single_track_audio_facts()) == (
        single_track_audio_facts(),
    )


def test_the_record_builder_drops_a_fact_it_cannot_admit_and_never_raises() -> None:
    """Control 7: a fact that fails is dropped and `facts_omitted` is set."""
    kept = _fact(_F.ASIN, _ASIN)
    forged = _fact(_F.CONTRIBUTOR, _NARRATOR)
    object.__setattr__(forged, "observed_value", "/home/someone/narrator")
    reading = dataclasses.replace(_marked_reading(), stripped=None)

    record = isolated_audio._byte_order_mark_record(
        reading, SidecarOutcome(), SidecarOutcome(facts=(kept, forged))
    )

    assert record == AudioSidecarByteOrderMark(
        facts=(AudioSidecarByteOrderMarkFact(_F.ASIN, _ASIN, _ASIN),),
        facts_omitted=True,
    )


def test_the_item_wide_cap_fills_in_name_order() -> None:
    """Control 8: at most 256 facts and the byte cap, in manifest order."""
    capped = isolated_audio._capped_records(_digests(250, 10, 3))

    assert [len(entry.byte_order_mark.facts) for entry in capped] == [250, 6, 0]  # type: ignore[union-attr]
    assert [entry.byte_order_mark.facts_omitted for entry in capped] == [  # type: ignore[union-attr]
        False,
        True,
        True,
    ]
    wide = dataclasses.replace(
        _digests(1)[0],
        byte_order_mark=AudioSidecarByteOrderMark(
            facts=tuple(
                AudioSidecarByteOrderMarkFact(
                    _F.DESCRIPTION, f"{index:03d}" + "\U0001d11e" * 509, None
                )
                for index in range(60)
            )
        ),
    )
    [bounded] = isolated_audio._capped_records((wide,))
    assert bounded.byte_order_mark is not None
    assert bounded.byte_order_mark.facts_omitted
    assert (
        len(canonical_json(bounded.byte_order_mark.to_data()).encode("utf-8"))
        <= MAX_BYTE_ORDER_MARK_BYTES
    )
    assert 0 < len(bounded.byte_order_mark.facts) < 60


def test_the_item_wide_byte_cap_counts_every_record_envelope() -> None:
    """Control 8: later records, and records with no facts, count too."""

    def wide(name: str, count: int) -> AudioSidecarDigest:
        return dataclasses.replace(
            _digests(1)[0],
            name=name,
            byte_order_mark=AudioSidecarByteOrderMark(
                facts=tuple(
                    AudioSidecarByteOrderMarkFact(
                        _F.DESCRIPTION, f"{index:03d}" + "x" * 497, None
                    )
                    for index in range(count)
                )
            ),
        )

    # The first record fills the cap to within one fact; the envelopes of the
    # fourteen after it, and of one with no facts left, must still fit.
    entries = (
        (wide("0.nfo", 140),)
        + tuple(wide(f"{index}.nfo", 1) for index in range(1, 15))
        + (
            dataclasses.replace(
                _digests(1)[0],
                name="15.nfo",
                byte_order_mark=AudioSidecarByteOrderMark(facts_omitted=True),
            ),
        )
    )

    capped = isolated_audio._capped_records(entries)

    assert (
        sum(
            len(canonical_json(entry.byte_order_mark.to_data()).encode("utf-8"))
            for entry in capped
            if entry.byte_order_mark is not None
        )
        <= MAX_BYTE_ORDER_MARK_BYTES
    )
    assert capped[0].byte_order_mark is not None
    assert capped[0].byte_order_mark.facts


def test_the_byte_cap_holds_when_records_without_facts_fill_it_exactly() -> None:
    """Control 8: a record left with no facts writes `false`, the longer flag."""
    empty = tuple(
        dataclasses.replace(
            _digests(1)[0],
            name=f"{index}.nfo",
            byte_order_mark=AudioSidecarByteOrderMark(),
        )
        for index in range(1, 8)
    )

    def total(tail: int) -> int:
        filler = dataclasses.replace(
            _digests(1)[0],
            name="0.nfo",
            byte_order_mark=AudioSidecarByteOrderMark(
                facts=tuple(
                    AudioSidecarByteOrderMarkFact(
                        _F.DESCRIPTION, f"{index:03d}" + "x" * 497, None
                    )
                    for index in range(114)
                )
                + (AudioSidecarByteOrderMarkFact(_F.DESCRIPTION, "y" * tail, None),)
            ),
        )
        return sum(
            len(canonical_json(entry.byte_order_mark.to_data()).encode("utf-8"))
            for entry in isolated_audio._capped_records((filler, *empty))
            if entry.byte_order_mark is not None
        )

    # Sweep the last fact's length so that, for one of them, it fills the
    # budget left after every envelope to the byte.
    totals = [total(tail) for tail in range(1, 513)]
    assert max(totals) <= MAX_BYTE_ORDER_MARK_BYTES
    assert max(totals) >= MAX_BYTE_ORDER_MARK_BYTES - 16


# --- Through the parent inspector (S4, S6, S7, controls 4, 7, 8, 9) ----------


class _BoundedChild(GrammarChild):
    """The grammar child, answering through the child's own protocol bound."""

    def __init__(self, maximum: int | None = None, *, blind: bool = False) -> None:
        super().__init__()
        self.maximum = maximum
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
        return AudioInspection.from_json(
            answer.to_json_within(
                len(answer.to_json()) * 4 if self.maximum is None else self.maximum,
            )
        )


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch) -> Iterator[_BoundedChild]:
    stand_in = _BoundedChild()
    monkeypatch.setattr(isolated_audio, "run_audio_inspector_child", stand_in)
    yield stand_in


def _context(own: tuple[str, ...]) -> AudioAssociationContext:
    return AudioAssociationContext(
        _TITLE,
        (
            TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (_AUTHOR,)),
            TargetField(
                TargetDestination.NARRATORS, TargetFieldState.VALUE, (_NARRATOR,)
            ),
        ),
        own,
    )


def _write(root: Path, documents: dict[str, str | bytes]) -> tuple[str, ...]:
    folder = root / "Item"
    folder.mkdir(exist_ok=True)
    (folder / _TRACK).write_bytes(b"synthetic audio")
    for name, text in documents.items():
        if isinstance(text, bytes):
            (folder / name).write_bytes(text)
        else:
            (folder / name).write_text(text, encoding="utf-8")
    return (_TRACK, *documents)


def _inspect(root: Path, documents: dict[str, str | bytes]) -> AudioInspectionResult:
    own = _write(root, documents)
    return IsolatedAudioInspector().inspect(
        MediaMountConfiguration("audiobooks", str(root), "/library", True),
        (TargetFile("1" * 64, f"Item/{_TRACK}", "m4b"),),
        _MAXIMUM,
        association=_context(own),
    )


def _record(
    result: AudioInspectionResult, name: str
) -> AudioSidecarByteOrderMark | None:
    [entry] = [value for value in result.audio.sidecars if value.name == name]
    return entry.byte_order_mark


def _nfo(*lines: str) -> str:
    return "".join(f"{line}\n" for line in lines)


def test_a_marked_nfo_titled_on_line_one_stays_refused_and_records_the_rest(
    tmp_path: Path, child: _BoundedChild
) -> None:
    """T6: `other_book` and no claims, as today; the record holds the rest."""
    result = _inspect(
        tmp_path,
        {
            "folder.nfo": _BOM
            + _nfo(
                f"Title: {_TITLE}",
                f"Author: {_AUTHOR}",
                "Narrator: Second Narrator",
                f"ASIN: {_ASIN}",
            )
        },
    )

    [entry] = result.audio.sidecars
    assert entry.refusals == (_R.OTHER_BOOK,)
    assert entry.claims == ()
    assert [claim for claim in result.claims if claim.basis == "sidecar"] == []
    assert entry.byte_order_mark == AudioSidecarByteOrderMark(
        facts=(
            AudioSidecarByteOrderMarkFact(
                _F.CONTRIBUTOR, "Second Narrator", "Second Narrator", "narrator"
            ),
            AudioSidecarByteOrderMarkFact(_F.ASIN, _ASIN, _ASIN),
        )
    )


def test_a_long_libation_summary_is_recorded_without_denying_the_item(
    tmp_path: Path, child: _BoundedChild
) -> None:
    """Control 7: a 500-character `publisher_summary` is recorded whole."""
    summary = "A synthetic summary sentence. " * 16 + "Ends here."
    summary = summary[:500]
    result = _inspect(
        tmp_path,
        {"book.metadata.json": _BOM + _libation(publisher_summary=summary)},
    )

    record = _record(result, "book.metadata.json")
    assert record is not None
    assert (_F.DESCRIPTION, summary.strip()) in {
        (fact.field, fact.observed) for fact in record.facts
    }
    assert not record.facts_omitted


def test_a_path_shaped_narrator_never_reaches_the_record(
    tmp_path: Path, child: _BoundedChild
) -> None:
    """Control 7: the item succeeds and the value is nowhere in the report."""
    result = _inspect(
        tmp_path,
        {
            "folder.nfo": _BOM
            + _nfo(
                "Narrator: /home/someone/secret-narrator",
                f"Title: {_TITLE}",
                f"Author: {_AUTHOR}",
            )
        },
    )

    assert _record(result, "folder.nfo") == AudioSidecarByteOrderMark()
    assert "secret-narrator" not in json.dumps(result.audio.to_data())


def test_an_unreadable_document_with_a_mark_has_no_record(
    tmp_path: Path, child: _BoundedChild
) -> None:
    """Control 1: a UTF-16 sidecar is dropped whole, so nothing is recorded."""
    result = _inspect(
        tmp_path,
        {"folder.nfo": b"\xff\xfe" + _nfo(f"Title: {_TITLE}").encode("utf-16-le")},
    )

    assert _record(result, "folder.nfo") is None


def test_an_answer_over_its_bound_drops_only_the_stripped_readings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control 4: the item succeeds; claims equal the twin's with no mark."""
    documents: dict[str, str | bytes] = {
        "book.metadata.json": _BOM + _libation(),
        "folder.nfo": _BOM
        + _nfo(f"ASIN: {_ASIN}", f"Title: {_TITLE}", f"Author: {_AUTHOR}"),
    }
    monkeypatch.setattr(isolated_audio, "run_audio_inspector_child", _BoundedChild(0))
    omitted = _inspect(tmp_path, documents)
    monkeypatch.setattr(
        isolated_audio, "run_audio_inspector_child", _BoundedChild(blind=True)
    )
    twin = _inspect(tmp_path, documents)

    assert omitted.claims == twin.claims
    for name in documents:
        assert _record(omitted, name) == AudioSidecarByteOrderMark(
            omitted=AudioSidecarStrippedLimit.CHILD_OUTPUT
        )


def test_a_fact_budget_cut_reaches_the_record(
    tmp_path: Path, child: _BoundedChild
) -> None:
    """Root addition: the record says the child's budget cut the reading."""
    result = _inspect(
        tmp_path,
        {
            "a.nfo": _BOM
            + _nfo(f"ASIN: {_ASIN}", f"Title: {_TITLE}", f"Author: {_AUTHOR}"),
            "b.nfo": _nfo(f"Title: {_TITLE}", f"Author: {_AUTHOR}")
            + _narrators("B", 300),
        },
    )

    record = _record(result, "a.nfo")
    assert record is not None
    assert record.omitted is AudioSidecarStrippedLimit.FACT_BUDGET


def test_an_audio_section_that_fits_only_without_records_succeeds(
    tmp_path: Path, child: _BoundedChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control 8, T9: full records, then facts emptied, then removed."""
    documents: dict[str, str | bytes] = {
        "folder.nfo": _BOM
        + _nfo(f"ASIN: {_ASIN}", f"Title: {_TITLE}", f"Author: {_AUTHOR}")
    }
    full = _inspect(tmp_path, documents)
    rungs = audio_record_rungs(full.audio)
    sizes = [len(canonical_json(rung.to_data()).encode("utf-8")) for rung in rungs]
    assert sizes[0] > sizes[1] > sizes[2]

    monkeypatch.setattr(isolated_audio, "MAX_AUDIO_BYTES", sizes[1])
    emptied = _inspect(tmp_path, documents)
    assert _record(emptied, "folder.nfo") == AudioSidecarByteOrderMark(
        facts_omitted=True
    )
    assert emptied.claims == full.claims

    monkeypatch.setattr(isolated_audio, "MAX_AUDIO_BYTES", sizes[2])
    removed = _inspect(tmp_path, documents)
    assert _record(removed, "folder.nfo") is None
    assert removed.claims == full.claims

    monkeypatch.setattr(isolated_audio, "MAX_AUDIO_BYTES", sizes[2] - 1)
    with pytest.raises(AudioItemInspectionError) as raised:
        _inspect(tmp_path, documents)
    assert raised.value.outcome is AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
    monkeypatch.setattr(
        isolated_audio, "run_audio_inspector_child", _BoundedChild(blind=True)
    )
    with pytest.raises(AudioItemInspectionError) as twin:
        _inspect(tmp_path, documents)
    assert twin.value.outcome is raised.value.outcome


def test_no_marked_value_or_association_text_reaches_a_log_or_stderr(
    tmp_path: Path,
    child: _BoundedChild,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T8, control 9: the stripped title, creators and `FILE` stay inside."""
    import logging

    caplog.set_level(logging.DEBUG)
    result = _inspect(
        tmp_path,
        {
            "book.nfo": _BOM
            + _nfo(
                f"Title: {_MARKER}",
                "Author: Zqx Marker Author",
                "Narrator: Zqx Marker Narrator",
            ),
            "book.cue": _BOM + 'FILE "Zqx Marker File.m4b" MP4\nTITLE "Other"\n',
        },
    )

    captured = capsys.readouterr()
    for text in (captured.out, captured.err, caplog.text):
        for marker in (
            _MARKER,
            "Zqx Marker Author",
            "Zqx Marker File",
            "Zqx Marker Narrator",
        ):
            assert marker not in text
    report = json.dumps(result.audio.to_data())
    for marker in (_MARKER, "Zqx Marker Author", "Zqx Marker File"):
        assert marker not in report
    assert all(_MARKER not in title.title for title in result.audio.discovery_titles)


_RECORDED_NARRATOR = "Zqx Recorded Narrator"
_RECORDED_ASIN = "B0ZQXMRK01"


def test_a_recorded_value_reaches_no_log_stderr_or_plan_query(
    tmp_path: Path,
    child: _BoundedChild,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T8, control 9: values the shadow pass records stay in the record.

    Both documents belong, so the reading without the mark keeps their hidden
    values and the record holds them; they still reach no output, no claim
    and no query of the discovery plan.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    result = _inspect(
        tmp_path,
        {
            "book.metadata.json": _BOM
            + _libation(_RECORDED_ASIN, narrators=[{"name": _RECORDED_NARRATOR}]),
            "book.nfo": _BOM
            + _nfo(
                f"Narrator: {_RECORDED_NARRATOR}",
                f"Title: {_TITLE}",
                f"Author: {_AUTHOR}",
            ),
        },
    )
    plan = audiobook_discovery_plan(
        TargetSnapshot(
            "audiobook",
            _TITLE,
            (TargetFile("1" * 64, f"Item/{_TRACK}", "m4b"),),
            fields=tuple(
                {value.destination: value for value in _context(()).fields}.get(
                    field.destination, field
                )
                for field in default_target_fields(_TITLE)
            ),
            own_file_names=(_TRACK, "book.metadata.json", "book.nfo"),
        ),
        (
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed=_TITLE,
                canonical=normalize_title(_TITLE),
                excerpt=_TITLE,
                applicability="not_applicable",
                basis="target_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                occurrence=0,
            ),
            *result.claims,
        ),
        audio=result.audio,
    )

    markers = (_RECORDED_ASIN, _RECORDED_NARRATOR)
    recorded = json.dumps(
        [
            entry.byte_order_mark.to_data()
            for entry in result.audio.sidecars
            if entry.byte_order_mark is not None
        ]
    )
    assert [marker for marker in markers if marker not in recorded] == []
    captured = capsys.readouterr()
    queries = repr(
        (
            plan.lanes,
            plan.local_claims,
            plan.work_title,
            plan.work_authors,
            plan.echoed_languages,
            plan.echoed_narrators,
            plan.companion_linked_asins,
        )
    )
    for text in (captured.out, captured.err, caplog.text, queries):
        for marker in markers:
            assert marker not in text
    claims = repr(
        [(claim.observed, claim.canonical, claim.reference) for claim in result.claims]
    )
    assert all(marker not in claims for marker in markers)
    assert plan.lanes


def _target_fields() -> tuple[TargetField, ...]:
    return default_target_fields(_TITLE)


def _report(
    audio: AudioFacts, result: AudioInspectionResult, *, providers=()
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", _target_fields()),
        file=FileFacts(0, "a" * 64, result.content_digest, "m4b"),
        local_claims=result.claims,
        local_reasons=tuple(
            sorted({f"{claim.basis}_{claim.kind.value}" for claim in result.claims})
        )
        or ("no_local_audio_claims",),
        providers=providers,
        audio=audio,
    )


def test_a_report_that_fits_only_without_record_facts_publishes(
    tmp_path: Path, child: _BoundedChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control 8: the whole-report bound runs the same steps in `create`.

    A provider-retry successor is built by the same `create` from its source's
    audio section, so it takes the same steps.
    """
    result = _inspect(
        tmp_path,
        {
            "folder.nfo": _BOM
            + _nfo(f"ASIN: {_ASIN}", f"Title: {_TITLE}", f"Author: {_AUTHOR}")
        },
    )
    sizes = [
        len(_report(rung, result).to_json().encode("utf-8"))
        for rung in audio_record_rungs(result.audio)
    ]
    assert sizes[0] > sizes[1] > sizes[2]

    monkeypatch.setattr(metadata_report, "MAX_REPORT_BYTES", sizes[1])
    published = _report(result.audio, result)
    assert published.audio is not None
    [entry] = published.audio.sidecars
    assert entry.byte_order_mark == AudioSidecarByteOrderMark(facts_omitted=True)
    successor = MetadataReport.create(
        target=published.target,
        file=published.file,
        local_claims=published.local_claims,
        local_reasons=published.local_reasons,
        providers=published.providers,
        audio=result.audio,
        library_languages=published.library_languages,
    )
    assert successor == published

    monkeypatch.setattr(metadata_report, "MAX_REPORT_BYTES", sizes[2])
    removed = _report(result.audio, result)
    assert removed.audio is not None
    assert removed.audio.sidecars[0].byte_order_mark is None

    monkeypatch.setattr(metadata_report, "MAX_REPORT_BYTES", sizes[2] - 1)
    twin = audio_record_rungs(result.audio)[-1]
    with pytest.raises(ReportValueError, match="byte limit"):
        _report(twin, result)
    with pytest.raises(ReportValueError, match="byte limit"):
        _report(result.audio, result)


def test_the_child_bound_is_written_through_the_protocol_helper() -> None:
    """Control 4: bootstrap gains the call only; the fallback is the domain's."""
    source = Path(audio_child.__file__).read_text(encoding="utf-8")
    assert "report.to_json_within(_STDOUT_LIMIT - 1)" in source
    assert ".stripped" not in source
    assert os.path.basename(audio_child.__file__) == "audio_child.py"


def test_a_mark_never_turns_an_item_without_evidence_into_a_success() -> None:
    """T5's outcome clause at the child: only plain readings make a success."""
    probe = json.dumps(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "chapters": [],
        }
    )

    def outcome(text: str) -> AudioInspectionOutcome:
        return interpret(
            probe,
            (AudioSidecar(_LIBATION, "book.metadata.json", text),),
            AudioPathContext(_TRACK),
            _SOURCE,
            AudioInspectionLimits(),
        ).outcome

    assert outcome(_libation()) is AudioInspectionOutcome.SUCCESS
    assert outcome(_BOM + _libation()) is AudioInspectionOutcome.NO_APPLICABLE_EVIDENCE


def test_a_record_holds_only_what_the_plain_reading_did_not_keep(
    tmp_path: Path, child: _BoundedChild
) -> None:
    """S5: the narrator is claimed already, so only the hidden ASIN is recorded."""
    result = _inspect(
        tmp_path,
        {
            "folder.nfo": _BOM
            + _nfo(
                f"ASIN: {_ASIN}",
                f"Title: {_TITLE}",
                f"Author: {_AUTHOR}",
                "Narrator: Second Narrator",
            )
        },
    )

    [entry] = result.audio.sidecars
    assert entry.refusals == ()
    assert [
        (claim.kind.value, claim.canonical)
        for claim in result.claims
        if claim.basis == "sidecar"
    ] == [("contributor", "Second Narrator")]
    assert entry.byte_order_mark == AudioSidecarByteOrderMark(
        facts=(AudioSidecarByteOrderMarkFact(_F.ASIN, _ASIN, _ASIN),)
    )


@pytest.mark.parametrize(
    ("hidden", "field"),
    [
        ("Language: English", _F.LANGUAGE),
        (f"Narrator: {_NARRATOR}", _F.CONTRIBUTOR),
    ],
    ids=["language", "narrator"],
)
def test_a_hidden_echo_never_reaches_the_echo_readers(
    tmp_path: Path, child: _BoundedChild, hidden: str, field: AudioInspectionField
) -> None:
    """#1481's refusal-only readers see only the plain reading's echoes.

    The carried-edition narrator refusal and the rich-metadata language gate
    read `echoed_target_values`. A target value echoed on a marked first line
    reaches neither; the same line without the mark does, so the comparison
    is not vacuous.
    """
    fields = (
        *_context(()).fields,
        TargetField(TargetDestination.LANGUAGE, TargetFieldState.VALUE, "English"),
    )

    def inspect(folder: Path, text: str) -> AudioInspectionResult:
        own = _write(folder, {"book.nfo": text})
        return IsolatedAudioInspector().inspect(
            MediaMountConfiguration("audiobooks", str(folder), "/library", True),
            (TargetFile("1" * 64, f"Item/{_TRACK}", "m4b"),),
            _MAXIMUM,
            association=AudioAssociationContext(_TITLE, fields, own),
        )

    text = _nfo(hidden, f"Title: {_TITLE}", f"Author: {_AUTHOR}")
    (tmp_path / "marked").mkdir()
    (tmp_path / "plain").mkdir()
    marked = inspect(tmp_path / "marked", _BOM + text)
    plain = inspect(tmp_path / "plain", text)

    [entry] = marked.audio.sidecars
    assert entry.echoed == ()
    assert entry.echoed_narrators == ()
    assert entry.byte_order_mark == AudioSidecarByteOrderMark()
    assert echoed_target_values(marked.audio, fields, field) == ()
    assert echoed_target_values(plain.audio, fields, field) != ()
