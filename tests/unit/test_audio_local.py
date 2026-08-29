"""Characterization of the pure audiobook interpretation grammar.

Every case here feeds ``interpret`` a recorded ffprobe document, so the module
under test is exercised without an ffprobe binary, a filesystem, or a socket.
"""

from __future__ import annotations

import json
from types import ModuleType

import pytest

from mechanical_librarian.adapters.evidence_extractors import audio_local
from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    AudioPathContext,
    AudioSidecar,
    interpret,
    sidecar_surface,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioEvidenceSurface,
    AudioInspectionField,
    AudioInspectionLimits,
    AudioInspectionOutcome,
    AudioPerformanceType,
    normalize_duration_seconds,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion

_SOURCE = Fingerprint("sha256", SchemaVersion("source-v1"), "a" * 64)
_LIMITS = AudioInspectionLimits()
_PATH = AudioPathContext("book.m4b")


def _probe(
    *,
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
    duration: str | None = "4.000",
    tags: dict[str, str] | None = None,
    streams: list[dict[str, object]] | None = None,
    chapters: list[dict[str, object]] | None = None,
) -> str:
    container: dict[str, object] = {"format_name": format_name}
    if duration is not None:
        container["duration"] = duration
    if tags is not None:
        container["tags"] = tags
    return json.dumps(
        {
            "format": container,
            "streams": streams
            if streams is not None
            else [{"codec_type": "audio", "codec_name": "aac"}],
            "chapters": chapters if chapters is not None else [],
        }
    )


def _read(
    document: str,
    *,
    sidecars: tuple[AudioSidecar, ...] = (),
    path: AudioPathContext = _PATH,
    limits: AudioInspectionLimits = _LIMITS,
) -> audio_local.AudioInterpretation:
    return interpret(document, sidecars, path, _SOURCE, limits)


def _values(
    interpretation: audio_local.AudioInterpretation, field: AudioInspectionField
) -> set[tuple[str, str]]:
    return {
        (value.surface.value, str(value.canonical_value))
        for value in interpretation.facts
        if value.field is field
    }


def test_interpretation_imports_nothing_that_reaches_the_world() -> None:
    assert {
        value.__name__
        for value in vars(audio_local).values()
        if type(value) is ModuleType
    } == {"json", "re"}


def test_container_duration_becomes_one_canonical_claim() -> None:
    interpretation = _read(_probe(duration="43200.000000"))

    assert interpretation.outcome is AudioInspectionOutcome.SUCCESS
    assert _values(interpretation, AudioInspectionField.DURATION) == {
        ("container_format", "43200.000")
    }
    assert normalize_duration_seconds("43200") == "43200.000"


@pytest.mark.parametrize("duration", ("0.000", "0", "not-a-duration", "-1", "1e3"))
def test_unusable_duration_produces_no_claim(duration: str) -> None:
    interpretation = _read(_probe(duration=duration))

    assert _values(interpretation, AudioInspectionField.DURATION) == set()


def test_stream_language_is_read_from_the_stream_surface() -> None:
    interpretation = _read(
        _probe(
            streams=[
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "tags": {"language": "eng"},
                }
            ]
        )
    )

    assert _values(interpretation, AudioInspectionField.LANGUAGE) == {
        ("container_stream", "eng")
    }


def test_mp4_tags_separate_named_atoms_from_freeform_ones() -> None:
    interpretation = _read(
        _probe(
            tags={
                "CDEK": "B0CW1KL2QD",
                "composer": "Recorded Narrator",
                "SERIES": "Discworld",
                "PART": "3",
                "----:com.pilabor.tone:NARRATOR": "Freeform Narrator",
            }
        )
    )

    assert _values(interpretation, AudioInspectionField.ASIN) == {
        ("mp4_ilst", "B0CW1KL2QD")
    }
    assert _values(interpretation, AudioInspectionField.SERIES) == {
        ("mp4_freeform_tone", "Discworld")
    }
    assert _values(interpretation, AudioInspectionField.SERIES_SEQUENCE) == {
        ("mp4_ilst", "3")
    }
    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == {
        ("mp4_ilst", "Recorded Narrator"),
        ("mp4_freeform_tone", "Freeform Narrator"),
    }
    assert {
        value.role
        for value in interpretation.facts
        if value.field is AudioInspectionField.CONTRIBUTOR
    } == {"narrator"}


def test_mp3_tags_separate_mapped_frames_from_user_text_frames() -> None:
    interpretation = _read(
        _probe(
            format_name="mp3",
            tags={
                "language": "eng",
                "composer": "Frame Narrator",
                "SERIES": "Discworld",
                "narrator": "User Text Narrator",
            },
            streams=[{"codec_type": "audio", "codec_name": "mp3"}],
        )
    )

    assert _values(interpretation, AudioInspectionField.LANGUAGE) == {
        ("id3_frame", "eng")
    }
    assert _values(interpretation, AudioInspectionField.SERIES) == {
        ("id3_user_text", "Discworld")
    }
    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == {
        ("id3_frame", "Frame Narrator"),
        ("id3_user_text", "User Text Narrator"),
    }


def test_album_never_becomes_a_series() -> None:
    interpretation = _read(_probe(tags={"album": "A Standalone Recording"}))

    assert _values(interpretation, AudioInspectionField.SERIES) == set()


@pytest.mark.parametrize("value", ("x", "1.2345", "01a", "-1"))
def test_unparseable_sequence_is_refused(value: str) -> None:
    interpretation = _read(_probe(tags={"series-part": value}))

    assert _values(interpretation, AudioInspectionField.SERIES_SEQUENCE) == set()


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    (
        ("ABRIDGED", "1", AudioPerformanceType.ABRIDGED),
        ("ABRIDGED", "no", AudioPerformanceType.UNABRIDGED),
        ("PERFORMANCETYPE", "Full Cast", AudioPerformanceType.FULL_CAST),
        ("PERFORMANCETYPE", "dramatized", AudioPerformanceType.DRAMATIZED),
        ("PERFORMANCETYPE", "unknown", AudioPerformanceType.UNKNOWN),
    ),
)
def test_performance_type_is_read_into_its_closed_vocabulary(
    key: str, value: str, expected: AudioPerformanceType
) -> None:
    interpretation = _read(_probe(tags={key: value}))

    assert _values(interpretation, AudioInspectionField.PERFORMANCE_TYPE) == {
        (
            "mp4_freeform_tone" if len(key) > 4 else "mp4_ilst",
            expected.value,
        )
    }


@pytest.mark.parametrize("value", ("maybe", "solo reading", ""))
def test_performance_type_outside_the_vocabulary_is_dropped(value: str) -> None:
    interpretation = _read(_probe(tags={"PERFORMANCETYPE": value}))

    assert _values(interpretation, AudioInspectionField.PERFORMANCE_TYPE) == set()


def test_bracketed_path_tokens_are_read_without_an_asin_shape_filter() -> None:
    interpretation = _read(
        _probe(),
        path=AudioPathContext("Book [0641977697].m4b", "Author - Title [B0CW1KL2QD]"),
    )

    assert _values(interpretation, AudioInspectionField.ASIN) == {
        ("path_file", "0641977697"),
        ("path_folder", "B0CW1KL2QD"),
    }


def test_chapters_are_bounded_structure_and_never_facts() -> None:
    interpretation = _read(
        _probe(
            chapters=[
                {"start_time": "0.000", "end_time": "10.500", "tags": {"title": "One"}},
                {
                    "start_time": "10.500",
                    "end_time": "20.000",
                    "tags": {"title": "Two"},
                },
                {"start_time": "20.000", "end_time": "30.000"},
            ]
        ),
        limits=AudioInspectionLimits(chapters=2),
    )

    assert interpretation.chapters_truncated is True
    assert [
        (value.ordinal, value.start_milliseconds, value.end_milliseconds, value.title)
        for value in interpretation.chapters
    ] == [(1, 0, 10_500, "One"), (2, 10_500, 20_000, "Two")]
    assert all(
        value.field is not AudioInspectionField.DESCRIPTION
        or "One" not in value.observed_value
        for value in interpretation.facts
    )


def test_unreadable_chapter_boundaries_are_skipped_without_renumbering() -> None:
    interpretation = _read(
        _probe(
            chapters=[
                {"start_time": "0.000", "end_time": "1.000"},
                {"start_time": "5.000", "end_time": "2.000"},
                {"start_time": "not-a-time", "end_time": "9.000"},
                {"start_time": "2.000", "end_time": "3.000"},
            ]
        )
    )

    assert [value.ordinal for value in interpretation.chapters] == [1, 2]
    assert interpretation.chapters_truncated is False


def test_embedded_cover_is_a_presence_fact_with_its_surface() -> None:
    interpretation = _read(
        _probe(
            streams=[
                {"codec_type": "audio", "codec_name": "aac"},
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "disposition": {"attached_pic": 1},
                },
            ]
        )
    )

    assert interpretation.embedded_cover.present is True
    assert (
        interpretation.embedded_cover.surface is AudioEvidenceSurface.CONTAINER_STREAM
    )
    assert not any(
        "cover" in value.field.value or "artifact" in value.supporting_context
        for value in interpretation.facts
    )


def test_absent_cover_carries_no_surface() -> None:
    interpretation = _read(_probe())

    assert interpretation.embedded_cover.present is False
    assert interpretation.embedded_cover.surface is None


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("metadata.json", AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF),
        ("book.metadata.json", AudioEvidenceSurface.SIDECAR_LIBATION),
        ("other.metadata.json", None),
        ("metadata.opf", AudioEvidenceSurface.SIDECAR_OPF),
        ("book.nfo", AudioEvidenceSurface.SIDECAR_NFO),
        ("book.cue", AudioEvidenceSurface.SIDECAR_CUE),
        ("cover.jpg", None),
        ("nested/metadata.json", None),
    ),
)
def test_sidecar_surface_admits_exactly_the_named_grammars(
    name: str, expected: AudioEvidenceSurface | None
) -> None:
    assert sidecar_surface(name, "book.m4b") is expected


def test_json_sidecar_reads_narrator_series_and_description() -> None:
    document = json.dumps(
        {
            "asin": "B0CW1KL2QD",
            "narrators": [{"name": "Recorded Narrator"}],
            "series": [{"name": "Discworld", "sequence": "3"}],
            "language": "english",
            "isAbridged": False,
            "publisherSummary": "A recorded summary.",
        }
    )
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_LIBATION, "book.metadata.json", document
            ),
        ),
    )

    assert _values(interpretation, AudioInspectionField.ASIN) == {
        ("sidecar_libation", "B0CW1KL2QD")
    }
    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == {
        ("sidecar_libation", "Recorded Narrator")
    }
    assert _values(interpretation, AudioInspectionField.SERIES) == {
        ("sidecar_libation", "Discworld")
    }
    assert _values(interpretation, AudioInspectionField.SERIES_SEQUENCE) == {
        ("sidecar_libation", "3")
    }
    assert _values(interpretation, AudioInspectionField.DESCRIPTION) == {
        ("sidecar_libation", "A recorded summary.")
    }
    assert _values(interpretation, AudioInspectionField.PERFORMANCE_TYPE) == {
        ("sidecar_libation", "unabridged")
    }


def test_opf_sidecar_is_read_as_a_recognition_grammar() -> None:
    document = (
        '<?xml version="1.0"?><package><metadata>'
        "<dc:language>eng</dc:language>"
        '<dc:contributor opf:role="nrt">Recorded &amp; Narrator</dc:contributor>'
        "<dc:description>A described recording.</dc:description>"
        '<meta name="calibre:series" content="Discworld"/>'
        '<meta name="calibre:series_index" content="3"/>'
        '<meta name="audible:asin" content="B0CW1KL2QD"/>'
        "</metadata></package>"
    )
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(AudioEvidenceSurface.SIDECAR_OPF, "metadata.opf", document),
        ),
    )

    assert _values(interpretation, AudioInspectionField.LANGUAGE) == {
        ("sidecar_opf", "eng")
    }
    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == {
        ("sidecar_opf", "Recorded & Narrator")
    }
    assert _values(interpretation, AudioInspectionField.SERIES) == {
        ("sidecar_opf", "Discworld")
    }
    assert _values(interpretation, AudioInspectionField.SERIES_SEQUENCE) == {
        ("sidecar_opf", "3")
    }
    assert _values(interpretation, AudioInspectionField.ASIN) == {
        ("sidecar_opf", "B0CW1KL2QD")
    }


def test_line_sidecars_read_their_own_key_grammars() -> None:
    nfo = _read(
        _probe(),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_NFO,
                "book.nfo",
                "Narrator: Recorded Narrator\nSeries: Discworld\nSeries Part: 3\n"
                "Abridged: yes\nUnrelated line without a colon\n",
            ),
        ),
    )
    cue = _read(
        _probe(),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_CUE,
                "book.cue",
                'PERFORMER "Recorded Narrator"\nREM SERIES "Discworld"\n'
                'TITLE "Ignored title"\n',
            ),
        ),
    )

    assert _values(nfo, AudioInspectionField.CONTRIBUTOR) == {
        ("sidecar_nfo", "Recorded Narrator")
    }
    assert _values(nfo, AudioInspectionField.SERIES_SEQUENCE) == {("sidecar_nfo", "3")}
    assert _values(nfo, AudioInspectionField.PERFORMANCE_TYPE) == {
        ("sidecar_nfo", "abridged")
    }
    assert _values(cue, AudioInspectionField.CONTRIBUTOR) == {
        ("sidecar_cue", "Recorded Narrator")
    }
    assert _values(cue, AudioInspectionField.SERIES) == {("sidecar_cue", "Discworld")}


def test_sidecars_beyond_the_configured_count_are_not_read() -> None:
    sidecars = tuple(
        AudioSidecar(
            AudioEvidenceSurface.SIDECAR_NFO,
            f"book-{index}.nfo",
            f"Series: Series {index}\n",
        )
        for index in range(4)
    )
    interpretation = _read(
        _probe(), sidecars=sidecars, limits=AudioInspectionLimits(sidecar_documents=2)
    )

    assert _values(interpretation, AudioInspectionField.SERIES) == {
        ("sidecar_nfo", "Series 0"),
        ("sidecar_nfo", "Series 1"),
    }


def test_one_surface_never_repeats_the_same_observation() -> None:
    interpretation = _read(
        _probe(tags={"narrator": "Recorded Narrator", "narrators": "Recorded Narrator"})
    )

    assert [
        value.surface.value
        for value in interpretation.facts
        if value.field is AudioInspectionField.CONTRIBUTOR
    ] == ["mp4_freeform_tone"]


def test_the_same_observation_on_two_surfaces_is_kept_once_per_surface() -> None:
    interpretation = _read(
        _probe(tags={"narrator": "Recorded Narrator"}),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_NFO,
                "book.nfo",
                "Narrator: Recorded Narrator\n",
            ),
        ),
    )

    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == {
        ("mp4_freeform_tone", "Recorded Narrator"),
        ("sidecar_nfo", "Recorded Narrator"),
    }


def test_interpretation_is_deterministic_for_one_document() -> None:
    document = _probe(
        tags={"CDEK": "B0CW1KL2QD", "narrator": "Recorded Narrator"},
        chapters=[{"start_time": "0.000", "end_time": "1.000"}],
    )

    first = _read(document)
    second = _read(document)

    assert [value.to_data() for value in first.facts] == [
        value.to_data() for value in second.facts
    ]


@pytest.mark.parametrize(
    "document",
    (
        "",
        "[]",
        "null",
        '{"format":"not-an-object","streams":[]}',
        '{"format":{},"streams":"not-a-list"}',
    ),
)
def test_unreadable_probe_documents_retain_no_evidence(document: str) -> None:
    interpretation = _read(document)

    assert interpretation.outcome.succeeded is False
    assert interpretation.facts == ()
    assert interpretation.chapters == ()
    assert interpretation.embedded_cover.present is False


def test_a_container_without_an_audio_stream_is_unsupported() -> None:
    interpretation = _read(
        _probe(streams=[{"codec_type": "video", "codec_name": "h264"}])
    )

    assert interpretation.outcome is AudioInspectionOutcome.UNSUPPORTED_FORMAT
    assert interpretation.facts == ()


def test_more_streams_than_the_limit_admits_is_malformed() -> None:
    interpretation = _read(
        _probe(streams=[{"codec_type": "audio"} for _ in range(4)]),
        limits=AudioInspectionLimits(streams=2),
    )

    assert interpretation.outcome is AudioInspectionOutcome.MALFORMED


def test_a_readable_container_without_evidence_reports_no_applicable_evidence() -> None:
    interpretation = _read(
        _probe(duration=None, tags={"encoder": "Lavf"}),
        path=AudioPathContext("book.m4b"),
    )

    assert interpretation.outcome is AudioInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert interpretation.facts == ()


def test_oversized_tag_values_are_dropped_rather_than_truncated() -> None:
    interpretation = _read(
        _probe(tags={"narrator": "n" * 64}),
        limits=AudioInspectionLimits(tag_value_characters=32),
    )

    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == set()


@pytest.mark.parametrize("width", (270, 271, 279, 280))
def test_a_long_observation_keeps_its_fact_even_without_room_for_its_key(
    width: int,
) -> None:
    """The observation is the fact; the key naming it is what gives way.

    A narrator at the full observed width leaves no room for the ``narrator: ``
    prefix inside the same bound, and the fact must survive that rather than
    being dropped whole.
    """
    narrator = "N" * width
    interpretation = _read(_probe(tags={"narrator": narrator}))

    supports = [
        value.supporting_context
        for value in interpretation.facts
        if value.field is AudioInspectionField.CONTRIBUTOR
    ]
    assert supports == [
        f"narrator: {narrator}" if len(narrator) + 10 <= 280 else narrator
    ]


def test_a_description_keeps_its_key_at_a_width_a_narrator_could_not() -> None:
    """The bound is the field's own, not one width for every fact."""
    description = "D" * 300
    interpretation = _read(_probe(tags={"description": description}))

    assert [
        value.supporting_context
        for value in interpretation.facts
        if value.field is AudioInspectionField.DESCRIPTION
    ] == [f"description: {description}"]


def test_reading_stops_at_the_tag_limit_rather_than_scanning_past_it() -> None:
    """The limit bounds the work, so it is where the loop ends."""
    tags = {f"{index:04d}filler": "value" for index in range(8)}
    tags["narrator"] = "Recorded Narrator"
    interpretation = _read(_probe(tags=tags), limits=AudioInspectionLimits(tags=4))

    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == set()
