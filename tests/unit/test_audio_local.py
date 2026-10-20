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
    NON_LINGUISTIC_LANGUAGE_CODES,
    AudioEvidenceSurface,
    AudioInspectionFact,
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
    chapters: object = None,
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


@pytest.mark.parametrize("language", ("English", "english", "en", "eng"))
def test_stream_language_is_read_from_the_stream_surface(language) -> None:
    interpretation = _read(
        _probe(
            streams=[
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "tags": {"language": language},
                }
            ]
        )
    )

    assert _values(interpretation, AudioInspectionField.LANGUAGE) == {
        ("container_stream", language)
    }


@pytest.mark.parametrize("language", ("UND", "und", "mul", "mis", "zxx", "xxx", "Xxx"))
def test_a_code_that_names_no_language_is_no_language_claim(language: str) -> None:
    """ISO 639-2's special codes say the language was not stated (#1250)."""
    stream = _read(
        _probe(
            streams=[
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "tags": {"language": language},
                }
            ]
        )
    )
    tagged = _read(
        _probe(format_name="mp3", tags={"language": language}),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF,
                "metadata.json",
                json.dumps({"language": language}),
            ),
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_OPF,
                "metadata.opf",
                f"<package><dc:language>{language}</dc:language></package>",
            ),
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_NFO, "book.nfo", f"Language: {language}\n"
            ),
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_CUE,
                "book.cue",
                f"REM LANGUAGE {language}\n",
            ),
        ),
    )

    assert _values(stream, AudioInspectionField.LANGUAGE) == set()
    assert _values(tagged, AudioInspectionField.LANGUAGE) == set()


def test_a_real_language_beside_a_placeholder_is_still_read() -> None:
    interpretation = _read(
        _probe(
            tags={"language": "eng"},
            streams=[
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "tags": {"language": "und"},
                }
            ],
        )
    )

    assert _values(interpretation, AudioInspectionField.LANGUAGE) == {
        ("mp4_freeform", "eng")
    }


@pytest.mark.parametrize("code", sorted(NON_LINGUISTIC_LANGUAGE_CODES))
@pytest.mark.parametrize("state", ("valid", "malformed"))
def test_a_language_fact_holding_a_placeholder_code_is_refused(
    code: str, state: str
) -> None:
    """The fact refuses it on its own, so a forged child fails closed."""
    with pytest.raises(ValueError, match="names no language"):
        AudioInspectionFact(
            AudioInspectionField.LANGUAGE,
            _SOURCE,
            code.upper(),
            code.upper() if state == "valid" else None,
            code.upper(),
            AudioEvidenceSurface.CONTAINER_STREAM,
            state,
        )


@pytest.mark.parametrize(
    "field",
    tuple(
        value
        for value in AudioInspectionField
        if value is not AudioInspectionField.DESCRIPTION
    ),
)
def test_only_a_description_fact_can_be_oversized(field: AudioInspectionField) -> None:
    with pytest.raises(ValueError, match="state is inconsistent"):
        AudioInspectionFact(
            field,
            _SOURCE,
            "value",
            None,
            "value",
            AudioEvidenceSurface.MP4_ILST,
            "oversized",
            "narrator" if field is AudioInspectionField.CONTRIBUTOR else None,
        )
    assert (
        AudioInspectionFact(
            AudioInspectionField.DESCRIPTION,
            _SOURCE,
            "value",
            None,
            "value",
            AudioEvidenceSurface.MP4_ILST,
            "oversized",
        ).state
        == "oversized"
    )


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
        ("mp4_freeform", "Discworld")
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
            "mp4_freeform" if len(key) > 4 else "mp4_ilst",
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
    assert interpretation.chapter_rows_omitted is True


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
    ] == ["mp4_freeform"]


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
        ("mp4_freeform", "Recorded Narrator"),
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


def _asins(
    interpretation: audio_local.AudioInterpretation,
) -> set[tuple[str, str, str | None, str]]:
    return {
        (value.surface.value, value.observed_value, value.canonical_value, value.state)
        for value in interpretation.facts
        if value.field is AudioInspectionField.ASIN
    }


@pytest.mark.parametrize(
    ("shape", "canonical"),
    (
        ("B0CW1KL2QD", "B0CW1KL2QD"),
        # An ISBN-10-shaped ASIN is exactly as well-formed as a `B0` one: the
        # gate is a shape, never a prefix.
        ("0641977697", "0641977697"),
        ("b0cw1kl2qd", "B0CW1KL2QD"),
    ),
)
def test_a_well_formed_asin_is_valid_and_canonical(shape: str, canonical: str) -> None:
    interpretation = _read(_probe(tags={"CDEK": shape}))

    assert _asins(interpretation) == {("mp4_ilst", shape, canonical, "valid")}


@pytest.mark.parametrize(
    "shape",
    ("B0CW1KL2Q", "B0CW1KL2QDX", "B0CW1KL-QD", "B0CW1KL2Q_", "", "not an asin"),
)
def test_a_malformed_asin_is_retained_rather_than_filtered(shape: str) -> None:
    """The file said it. The report says the file said it, and that it is junk."""
    interpretation = _read(_probe(tags={"CDEK": shape}))

    cleaned = " ".join(shape.split())
    assert _asins(interpretation) == (
        {("mp4_ilst", cleaned, None, "malformed")} if cleaned else set()
    )


def test_every_asin_surface_passes_the_same_gate() -> None:
    """One choke point, so no surface can acquire its own idea of an ASIN."""
    opf = (
        '<?xml version="1.0"?><package><metadata>'
        '<meta name="audible:asin" content="B0CW1KL-QD"/>'
        "</metadata></package>"
    )
    interpretation = _read(
        _probe(format_name="mp3", tags={"asin": "B0CW1KL-QD"}),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF,
                "metadata.json",
                json.dumps({"asin": "B0CW1KL-QD"}),
            ),
            AudioSidecar(AudioEvidenceSurface.SIDECAR_OPF, "metadata.opf", opf),
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_NFO, "book.nfo", "ASIN: B0CW1KL-QD\n"
            ),
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_CUE, "book.cue", "REM ASIN B0CW1KL-QD\n"
            ),
        ),
    )

    assert {
        (surface, state)
        for surface, _observed, _canonical, state in _asins(interpretation)
    } == {
        ("id3_user_text", "malformed"),
        ("sidecar_audiobookshelf", "malformed"),
        ("sidecar_opf", "malformed"),
        ("sidecar_nfo", "malformed"),
        ("sidecar_cue", "malformed"),
    }


def test_a_path_token_cannot_be_malformed_because_its_grammar_is_the_shape() -> None:
    interpretation = _read(
        _probe(), path=AudioPathContext("Book [0641977697].m4b", "Folder [B0CW1KL2QD]")
    )

    assert _asins(interpretation) == {
        ("path_file", "0641977697", "0641977697", "valid"),
        ("path_folder", "B0CW1KL2QD", "B0CW1KL2QD", "valid"),
    }


def test_a_junk_asin_beside_a_good_one_keeps_both_on_their_own_surfaces() -> None:
    interpretation = _read(_probe(tags={"CDEK": "B0CW1KL2QD", "asin": "junk"}))

    assert _asins(interpretation) == {
        ("mp4_ilst", "B0CW1KL2QD", "B0CW1KL2QD", "valid"),
        ("mp4_ilst", "junk", None, "malformed"),
    }


def test_an_opf_contributor_is_narrator_evidence_only_when_its_role_says_so() -> None:
    document = (
        '<?xml version="1.0"?><package><metadata>'
        '<dc:contributor opf:role="nrt">Named Narrator</dc:contributor>'
        '<dc:contributor opf:role="prf">Named Performer</dc:contributor>'
        '<dc:contributor opf:role="edt">Named Editor</dc:contributor>'
        "<dc:contributor>Unnamed Role</dc:contributor>"
        '<dc:contributor role="NRT">Bare &amp; Attribute</dc:contributor>'
        "</metadata></package>"
    )
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(AudioEvidenceSurface.SIDECAR_OPF, "metadata.opf", document),
        ),
    )

    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == {
        ("sidecar_opf", "Named Narrator"),
        ("sidecar_opf", "Named Performer"),
        ("sidecar_opf", "Bare & Attribute"),
    }


def test_an_mp4_freeform_atom_is_attributed_to_tone_only_when_it_says_tone() -> None:
    interpretation = _read(
        _probe(
            tags={
                "----:com.pilabor.tone:SERIES": "Tone Series",
                "----:com.apple.iTunes:SHOW": "Apple Series",
                "movementname": "Unmeant Series",
            }
        )
    )

    assert _values(interpretation, AudioInspectionField.SERIES) == {
        ("mp4_freeform_tone", "Tone Series"),
        ("mp4_freeform", "Apple Series"),
        ("mp4_freeform", "Unmeant Series"),
    }


def test_naming_the_freeform_surface_changes_no_basis_and_no_claim() -> None:
    """The correction is a label. Bases, values and roles are byte-identical."""
    interpretation = _read(
        _probe(
            tags={
                "----:com.apple.iTunes:NARRATOR": "Apple Narrator",
                "narrator": "Bare Narrator",
            }
        )
    )

    assert {
        (
            value.field.value,
            value.basis,
            value.observed_value,
            value.canonical_value,
            value.role,
        )
        for value in interpretation.facts
        if value.field is AudioInspectionField.CONTRIBUTOR
    } == {
        ("contributor", "embedded_tag", "Apple Narrator", "Apple Narrator", "narrator"),
        ("contributor", "embedded_tag", "Bare Narrator", "Bare Narrator", "narrator"),
    }


def test_a_role_spelled_inside_another_attributes_value_is_not_a_role() -> None:
    """An attribute's value is text the file chose. Only a name is a name."""
    document = (
        '<?xml version="1.0"?><package><metadata>'
        "<dc:contributor title=\"fake role='nrt'\">Impostor</dc:contributor>"
        "</metadata></package>"
    )
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(AudioEvidenceSurface.SIDECAR_OPF, "metadata.opf", document),
        ),
    )

    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == set()


@pytest.mark.parametrize(
    "attributes",
    (
        ' title="x" opf:role="nrt"',
        " opf:role='nrt' title='x'",
        ' id="a" role="NRT" title="x"',
        '\n  opf:role = "nrt"\n  title="x"',
        ' title="role=\'edt\'" opf:role="nrt"',
    ),
)
def test_a_real_role_is_found_wherever_the_attributes_put_it(attributes: str) -> None:
    document = (
        '<?xml version="1.0"?><package><metadata>'
        f"<dc:contributor{attributes}>Named Narrator</dc:contributor>"
        "</metadata></package>"
    )
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(AudioEvidenceSurface.SIDECAR_OPF, "metadata.opf", document),
        ),
    )

    assert _values(interpretation, AudioInspectionField.CONTRIBUTOR) == {
        ("sidecar_opf", "Named Narrator")
    }


@pytest.mark.parametrize("shape", ("ß12345678", "İ1234567", "ﬀ12345678"))
def test_a_case_mapping_can_never_grow_a_value_into_an_asin(shape: str) -> None:
    """`ß`.upper() is `SS`. Ten characters after that is not ten characters."""
    interpretation = _read(_probe(tags={"CDEK": shape}))

    assert _asins(interpretation) == {("mp4_ilst", shape, None, "malformed")}


def test_an_attribute_run_stops_being_read_where_it_stops_making_sense() -> None:
    """Past a token that is not a quoted pair, nothing is being understood."""
    assert audio_local._xml_attributes(' title="x" bare opf:role="nrt"') == {
        "title": "x"
    }
    assert audio_local._xml_attributes(' role="nrt" role="edt"') == {"role": "nrt"}
    assert audio_local._is_narrator_role(' data-role="nrt"') is False


def test_a_track_title_is_projected_whole_or_not_at_all() -> None:
    """A cut title names the chapter something the file does not say.

    The composition falls back to the exported file name when a track states
    no usable title, and that name is at least a real one; a prefix of an
    oversize title is text no surface ever carried.
    """
    assert _read(_probe(tags={"title": "Part One"})).title == "Part One"
    assert _read(_probe(tags={"title": " Part   One "})).title == "Part One"
    assert _read(_probe(tags={"title": "x" * 300})).title is None


@pytest.mark.parametrize(
    "bad_row",
    (
        None,
        {},
        {"start_time": "bad", "end_time": "3"},
        {"start_time": "4", "end_time": "3"},
    ),
)
@pytest.mark.parametrize("position", (0, 1, 2))
def test_partial_chapters_remain_visible_and_record_omission(bad_row, position) -> None:
    rows: list[object] = [
        {"start_time": "0", "end_time": "1"},
        {"start_time": "1", "end_time": "2"},
    ]
    rows.insert(position, bad_row)
    result = _read(_probe(chapters=rows))
    assert [(c.start_milliseconds, c.end_milliseconds) for c in result.chapters] == [
        (0, 1000),
        (1000, 2000),
    ]
    assert result.chapter_rows_omitted is True
    assert result.chapters_truncated is False


@pytest.mark.parametrize(
    "rows",
    (
        [],
        [{"start_time": "0", "end_time": "0"}],
        [{"start_time": "0", "end_time": "1"}, {"start_time": "2", "end_time": "3"}],
    ),
)
def test_retained_empty_zero_and_gapped_chapters_do_not_invent_omission(rows) -> None:
    result = _read(_probe(chapters=rows))
    assert len(result.chapters) == len(rows)
    assert result.chapter_rows_omitted is False


def test_chapter_overflow_and_examined_omission_are_independent() -> None:
    result = _read(
        _probe(chapters=[None, {"start_time": "0", "end_time": "1"}, None]),
        limits=AudioInspectionLimits(chapters=2),
    )
    assert len(result.chapters) == 1
    assert result.chapter_rows_omitted is True
    assert result.chapters_truncated is True
    unexamined = _read(
        _probe(chapters=[{"start_time": "0", "end_time": "1"}, None]),
        limits=AudioInspectionLimits(chapters=1),
    )
    assert unexamined.chapter_rows_omitted is False
    assert unexamined.chapters_truncated is True


def test_an_audible_product_id_is_not_an_asin() -> None:
    """``prID`` is a product id, never an ASIN, so it is no ASIN claim (#1250)."""
    interpretation = _read(_probe(tags={"prID": "123456", "asin": "B0CW1KL2QD"}))

    assert _asins(interpretation) == {("mp4_ilst", "B0CW1KL2QD", "B0CW1KL2QD", "valid")}


def _descriptions(
    interpretation: audio_local.AudioInterpretation,
) -> list[tuple[str, str | None, str, str]]:
    return [
        (
            value.observed_value,
            value.canonical_value,
            value.state,
            value.supporting_context,
        )
        for value in interpretation.facts
        if value.field is AudioInspectionField.DESCRIPTION
    ]


def test_a_description_at_the_bound_is_valid_and_whole() -> None:
    description = "D" * 512
    interpretation = _read(_probe(tags={"description": description}))

    assert _descriptions(interpretation) == [
        (description, description, "valid", description)
    ]


@pytest.mark.parametrize("width", (513, 600))
def test_a_longer_description_keeps_its_first_512_characters_as_oversized(
    width: int,
) -> None:
    """The prefix is evidence a description exists, never a value (#1250)."""
    description = "D" * width
    interpretation = _read(_probe(tags={"comment": description}))

    assert _descriptions(interpretation) == [("D" * 512, None, "oversized", "D" * 512)]


@pytest.mark.parametrize("character", ("é", "\U0001f600"))
def test_a_cut_is_made_on_code_points_and_stays_within_its_byte_bound(
    character: str,
) -> None:
    interpretation = _read(_probe(tags={"description": character * 513}))

    [(observed, canonical, state, _support)] = _descriptions(interpretation)
    assert (observed, canonical, state) == (character * 512, None, "oversized")
    assert len(observed.encode("utf-8")) <= 4 * 512


def test_a_cut_through_a_decomposed_character_loses_only_the_tail() -> None:
    description = "a" * 511 + "e\u0301" + "b" * 40
    interpretation = _read(_probe(tags={"description": description}))

    [(observed, _canonical, state, _support)] = _descriptions(interpretation)
    assert state == "oversized"
    assert observed == "a" * 511 + "e"
    assert description.startswith(observed)


def test_whitespace_that_collapses_under_the_bound_is_not_a_cut() -> None:
    description = "word    " * 100
    interpretation = _read(_probe(tags={"description": description}))

    collapsed = " ".join(description.split())
    assert len(description) > 512 >= len(collapsed)
    assert _descriptions(interpretation) == [
        (collapsed, collapsed, "valid", f"description: {collapsed}")
    ]


@pytest.mark.parametrize(
    "hidden",
    (
        " api_key=synthetic",
        " /Users/owner/Audiobooks/book.m4b",
        " /home/owner/media",
        " control \x9b text",
        " surrogate \ud800 text",
    ),
)
def test_a_value_is_validated_whole_before_it_is_cut(hidden: str) -> None:
    """What lies past character 512 still refuses the value (#1250)."""
    description = "D" * 600 + hidden
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF,
                "metadata.json",
                json.dumps({"description": description}),
            ),
        ),
    )

    assert _descriptions(interpretation) == []


def test_a_description_past_the_tag_width_is_no_claim() -> None:
    limits = AudioInspectionLimits(tag_value_characters=1024)
    description = "D" * 1025
    interpretation = _read(
        _probe(tags={"description": description}),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_LIBATION,
                "book.metadata.json",
                json.dumps({"publisherSummary": description}),
            ),
        ),
        limits=limits,
    )

    assert _descriptions(interpretation) == []
    assert audio_local.bounded_description(description, limits) is None
    assert audio_local.bounded_description("D" * 1024, limits) == (
        "D" * 512,
        "oversized",
    )


def test_sidecar_descriptions_past_the_bound_are_oversized_too() -> None:
    description = "S" * 700
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_LIBATION,
                "book.metadata.json",
                json.dumps({"publisherSummary": description}),
            ),
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_OPF,
                "metadata.opf",
                f"<package><dc:description>{description}</dc:description></package>",
            ),
        ),
    )

    assert {
        (value.surface.value, value.observed_value, value.state)
        for value in interpretation.facts
        if value.field is AudioInspectionField.DESCRIPTION
    } == {
        ("sidecar_libation", "S" * 512, "oversized"),
        ("sidecar_opf", "S" * 512, "oversized"),
    }


def test_a_line_sidecar_description_past_its_line_grammar_is_still_unread() -> None:
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_NFO, "book.nfo", f"Plot: {'P' * 600}\n"
            ),
        ),
    )

    assert _descriptions(interpretation) == []


def test_an_oversized_prefix_and_a_whole_value_are_two_observations() -> None:
    """The cut prefix and a whole 512-character value share text, not state."""
    interpretation = _read(
        _probe(),
        sidecars=(
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF,
                "metadata.json",
                json.dumps({"description": "D" * 512, "publisherSummary": "D" * 600}),
            ),
        ),
    )

    assert sorted(
        (value.state, value.canonical_value is None)
        for value in interpretation.facts
        if value.field is AudioInspectionField.DESCRIPTION
    ) == [("oversized", True), ("valid", False)]
