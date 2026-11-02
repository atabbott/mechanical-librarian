"""The composed audiobook inspector over a real probe binary and real media.

This is the one cohort that executes ffprobe. It builds each container in
code, writes it into a temporary mount, and drives the same
``IsolatedAudioInspector`` the application composes, so the probe child, the
interpretation child, the sidecar descriptors, and the path grammar are all
proved together against bytes rather than against a recorded document.

The tier requires the same ffmpeg package the shipped image pins. A missing
probe fails loudly here rather than skipping: a silent skip would report this
evidence as covered when nothing ran.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    _PROBE_PATHS,
    resolve_probe_path,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    AudioItemInspectionError,
    TargetFile,
)
from mechanical_librarian.application.review_pipeline import (
    audio_inspection_failure_code,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioChapterComposition,
    AudioInspectionOutcome,
    AudioObservationKind,
)
from mechanical_librarian.domain.metadata_report import ClaimKind, FieldState
from tests.support.audio_containers import (
    AC3_OBJECT_TYPE,
    Mp4Fixture,
    hostile_container,
    mp3_audiobook,
    mp4_audiobook,
    multi_track_mp3,
)

_MAXIMUM_BYTES = 268_435_456


def _require_probe() -> str:
    binary = resolve_probe_path()
    if binary is None:
        raise AssertionError(
            "this tier executes the shipped probe and found none at "
            + ", ".join(_PROBE_PATHS)
            + ": install the pinned ffmpeg package and run the tier again"
        )
    return binary


def _mount(root: Path) -> MediaMountConfiguration:
    return MediaMountConfiguration("audiobooks", str(root), "/library", True)


def _track(relative_path: str, ordinal: int = 1) -> TargetFile:
    return TargetFile(
        f"{ordinal:064x}",
        relative_path,
        relative_path.rsplit(".", 1)[-1],
        ordinal,
    )


def _inspect(root: Path, relative_path: str) -> AudioInspectionResult:
    return IsolatedAudioInspector().inspect(
        _mount(root), (_track(relative_path),), _MAXIMUM_BYTES
    )


def _inspect_set(root: Path, names: tuple[str, ...]) -> AudioInspectionResult:
    return IsolatedAudioInspector().inspect(
        _mount(root),
        tuple(_track(name, ordinal) for ordinal, name in enumerate(names, 1)),
        _MAXIMUM_BYTES,
    )


def _claims(result: AudioInspectionResult) -> set[tuple[str, str, str]]:
    return {(value.kind.value, value.observed, value.basis) for value in result.claims}


@pytest.fixture
def media_root() -> Iterator[Path]:
    _require_probe()
    with tempfile.TemporaryDirectory() as temporary:
        yield Path(temporary).resolve()


def test_a_generated_audiobook_yields_one_closed_local_evidence_set(
    media_root: Path,
) -> None:
    folder = media_root / "Ada Lovelace - Recorded Title [B0CW1KL2QD]"
    folder.mkdir()
    (folder / "book.m4b").write_bytes(
        mp4_audiobook(
            Mp4Fixture(
                milliseconds=43_200_000,
                language="eng",
                well_known={
                    "CDEK": "B0CW1KL2QD",
                    "©wrt": "Recorded Narrator",
                    "desc": "A recorded summary.",
                },
                freeform={
                    ("com.pilabor.tone", "SERIES"): "Discworld",
                    ("com.pilabor.tone", "PART"): "3",
                    ("com.pilabor.tone", "ABRIDGED"): "0",
                },
                chapters=((0, "One"), (10_000, "Two")),
                embedded_cover=True,
            )
        )
    )
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "asin": "B0CW1KL2QD",
                "narrators": ["Sidecar Narrator"],
                "seriesName": "Discworld",
                "description": "A sidecar summary.",
            }
        ),
        encoding="utf-8",
    )

    result = _inspect(media_root, f"{folder.name}/book.m4b")

    observed = _claims(result)
    assert ("asin", "B0CW1KL2QD", "embedded_tag") in observed
    assert ("asin", "B0CW1KL2QD", "path") in observed
    assert ("asin", "B0CW1KL2QD", "sidecar") in observed
    assert ("contributor", "Recorded Narrator", "embedded_tag") in observed
    assert ("contributor", "Sidecar Narrator", "sidecar") in observed
    assert ("series", "Discworld", "embedded_tag") in observed
    assert ("series_sequence", "3", "embedded_tag") in observed
    assert ("duration", "43200.000000", "embedded_tag") in observed
    assert ("language", "eng", "embedded_tag") in observed
    assert ("description", "A recorded summary.", "embedded_tag") in observed
    assert ("description", "A sidecar summary.", "sidecar") in observed

    duration = next(
        value for value in result.claims if value.kind is ClaimKind.DURATION
    )
    assert duration.canonical == "43200.000"
    performance = next(
        value for value in result.claims if value.kind is ClaimKind.PERFORMANCE_TYPE
    )
    assert performance.canonical == "unabridged"
    assert {
        value.role for value in result.claims if value.kind is ClaimKind.CONTRIBUTOR
    } == {"narrator"}
    assert [
        (value.ordinal, value.start_milliseconds, value.title)
        for value in result.audio.chapters
    ] == [(1, 0, "One"), (2, 10_000, "Two")]
    assert result.audio.chapters_truncated is False
    assert result.audio.embedded_cover.present is True
    assert len(result.content_digest) == 64


def test_a_long_comment_becomes_an_oversized_prefix_and_und_no_language(
    media_root: Path,
) -> None:
    """The probe's real output, cut on the parent's terms (#1250)."""
    comment = ("A long recorded comment " * 25)[:599] + "."
    assert len(comment) == 600
    (media_root / "book.m4b").write_bytes(
        mp4_audiobook(
            Mp4Fixture(
                language="und",
                well_known={"©cmt": comment, "prID": "123456789"},
            )
        )
    )

    result = _inspect(media_root, "book.m4b")

    [description] = [
        value for value in result.claims if value.kind is ClaimKind.DESCRIPTION
    ]
    assert description.state is FieldState.OVERSIZED
    assert description.canonical is None
    assert description.observed == comment[:512]
    assert description.observed[-1] != " "
    assert not any(
        value.kind in {ClaimKind.LANGUAGE, ClaimKind.ASIN} for value in result.claims
    )


def test_an_mp3_audiobook_separates_mapped_frames_from_user_text(
    media_root: Path,
) -> None:
    (media_root / "book.mp3").write_bytes(
        mp3_audiobook(
            seconds=4.0,
            frames={"TLAN": "eng", "TCOM": "Frame Narrator"},
            user_text={"SERIES": "Discworld", "NARRATOR": "User Narrator"},
        )
    )

    result = _inspect(media_root, "book.mp3")

    observed = _claims(result)
    assert ("language", "eng", "embedded_tag") in observed
    assert ("contributor", "Frame Narrator", "embedded_tag") in observed
    assert ("contributor", "User Narrator", "embedded_tag") in observed
    assert ("series", "Discworld", "embedded_tag") in observed
    assert result.audio.chapters == ()
    assert result.audio.embedded_cover.present is False


def test_a_hostile_container_is_refused_without_retaining_evidence(
    media_root: Path,
) -> None:
    (media_root / "book.m4b").write_bytes(hostile_container())

    with pytest.raises(AudioItemInspectionError) as raised:
        _inspect(media_root, "book.m4b")

    assert raised.value.outcome in {
        AudioInspectionOutcome.PROBE_FAILURE,
        AudioInspectionOutcome.UNSUPPORTED_FORMAT,
        AudioInspectionOutcome.MALFORMED,
    }


def test_an_unusable_sidecar_does_not_deny_the_rest_of_the_evidence(
    media_root: Path,
) -> None:
    (media_root / "book.m4b").write_bytes(
        mp4_audiobook(Mp4Fixture(well_known={"CDEK": "B0CW1KL2QD"}))
    )
    (media_root / "book.nfo").write_text("n" * 2_097_152, encoding="utf-8")
    (media_root / "cover.jpg").write_bytes(b"not a sidecar grammar")

    result = _inspect(media_root, "book.m4b")

    assert ("asin", "B0CW1KL2QD", "embedded_tag") in _claims(result)
    assert all(value.basis != "sidecar" for value in result.claims)


def test_a_single_file_item_replays_to_the_same_composed_document(
    media_root: Path,
) -> None:
    """The one-track case of the composed shape, and stable across replays.

    A v35 single-file report is the v34 evidence plus a composition kind and
    the one-entry manifest that names the file it was read from.
    """
    (media_root / "book.m4b").write_bytes(
        mp4_audiobook(Mp4Fixture(milliseconds=4_000, well_known={"CDEK": "B0CW1KL2QD"}))
    )

    first = _inspect(media_root, "book.m4b")
    second = _inspect(media_root, "book.m4b")

    assert first.audio.composition is AudioChapterComposition.SINGLE_FILE
    assert [value.ordinal for value in first.audio.tracks] == [1]
    assert first.audio.tracks[0].name == "book.m4b"
    assert first.audio.tracks[0].digest == first.content_digest
    assert first.audio.to_data() == second.audio.to_data()
    assert _claims(first) == _claims(second)


def test_a_multi_track_item_composes_one_running_time_and_per_track_chapters(
    media_root: Path,
) -> None:
    """Three real MP3 tracks, probed one at a time, published as one item."""
    folder = media_root / "Ada Lovelace - Recorded Title"
    folder.mkdir()
    tracks = multi_track_mp3(
        3,
        seconds=2.0,
        frames={"TLAN": "eng"},
        user_text={"SERIES": "Discworld", "NARRATOR": "Recorded Narrator"},
    )
    for name, payload in tracks:
        (folder / name).write_bytes(payload)

    result = _inspect_set(
        media_root, tuple(f"{folder.name}/{name}" for name, _payload in tracks)
    )

    observed = _claims(result)
    assert ("series", "Discworld", "embedded_tag") in observed
    assert ("contributor", "Recorded Narrator", "embedded_tag") in observed
    durations = [value for value in result.claims if value.kind is ClaimKind.DURATION]
    assert len(durations) == 1
    assert durations[0].canonical is not None
    assert 5.0 < float(durations[0].canonical) < 7.0
    assert result.audio.composition is AudioChapterComposition.PER_TRACK
    assert [value.title for value in result.audio.chapters] == [
        "Part 1",
        "Part 2",
        "Part 3",
    ]
    assert [value.ordinal for value in result.audio.tracks] == [1, 2, 3]
    assert all(value.claims for value in result.audio.tracks), (
        "each track carries the composed claims it stated"
    )


def test_a_disagreeing_track_keeps_both_values_and_records_the_observation(
    media_root: Path,
) -> None:
    tracks = multi_track_mp3(
        2,
        seconds=1.0,
        user_text={"ASIN": "B0CW1KL2QD"},
        per_track_user_text={2: {"ASIN": "B0DIFFEREN"}},
    )
    for name, payload in tracks:
        (media_root / name).write_bytes(payload)

    result = _inspect_set(media_root, tuple(name for name, _payload in tracks))

    assert {
        value.observed for value in result.claims if value.kind is ClaimKind.ASIN
    } == {"B0CW1KL2QD", "B0DIFFEREN"}
    assert {value.kind for value in result.audio.observations} >= {
        AudioObservationKind.TAG_DISAGREES_ACROSS_TRACKS,
        AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS,
    }


def test_one_hostile_track_denies_the_whole_item_its_evidence(
    media_root: Path,
) -> None:
    """No partial evidence: an item is one closed set or none at all."""
    tracks = multi_track_mp3(2, seconds=1.0)
    for name, payload in tracks:
        (media_root / name).write_bytes(payload)
    (media_root / "03.m4b").write_bytes(hostile_container())

    with pytest.raises(AudioItemInspectionError) as raised:
        _inspect_set(media_root, ("01.mp3", "02.mp3", "03.m4b"))

    assert raised.value.outcome in {
        AudioInspectionOutcome.PROBE_FAILURE,
        AudioInspectionOutcome.UNSUPPORTED_FORMAT,
        AudioInspectionOutcome.MALFORMED,
    }
    assert raised.value.track == 3


def test_a_zero_byte_file_is_unreadable_not_refused(media_root: Path) -> None:
    """An empty file has nothing to parse, so nothing may say parsing refused.

    The probe binary is real here: handed the empty file it fails, and before
    this gate that failure reached the owner as `parser_refused` -- advice to
    supply a file within the supported parsing limits, about a file with no
    bytes. The gate reads the length the open already returned, so the item
    ends `unreadable_media` under the empty track's own ordinal.
    """
    tracks = multi_track_mp3(2, seconds=1.0)
    for name, payload in tracks:
        (media_root / name).write_bytes(payload)
    empty = tracks[1][0]
    (media_root / empty).write_bytes(b"")

    with pytest.raises(AudioItemInspectionError) as raised:
        _inspect_set(media_root, tuple(name for name, _payload in tracks))

    assert raised.value.outcome is AudioInspectionOutcome.UNAVAILABLE_SOURCE
    assert raised.value.track == 2
    assert audio_inspection_failure_code(raised.value.outcome) == "unreadable_media"


def _observed_kinds(result: AudioInspectionResult) -> set[AudioObservationKind]:
    return {value.kind for value in result.audio.observations}


# What every container case below departs from: an ordinary generated audiobook
# that states one thing this build did not expect, and nothing else.
_HEALTHY_FIXTURE = Mp4Fixture(
    milliseconds=4_000,
    well_known={"CDEK": "B0CW1KL2QD", "©nam": "Recorded Title"},
)


def test_a_healthy_container_states_no_media_departure(media_root: Path) -> None:
    """The control the four cases below are read against.

    Every one of them changes exactly one header, so this proves the change is
    what the observation came from rather than something the generator does.
    """
    (media_root / "book.m4b").write_bytes(mp4_audiobook(_HEALTHY_FIXTURE))

    result = _inspect(media_root, "book.m4b")

    assert result.audio.observations == ()
    facts = result.audio.tracks[0]
    assert facts.format == "m4b"


@pytest.mark.parametrize(
    ("case", "name", "written", "kind"),
    (
        pytest.param(
            "codec",
            "book.m4b",
            None,
            AudioObservationKind.CODEC_UNEXPECTED,
            id="an-esds-naming-a-codec-this-build-did-not-expect",
        ),
        pytest.param(
            "container",
            "book.m4b",
            None,
            AudioObservationKind.CONTAINER_UNEXPECTED,
            id="an-m4b-that-is-really-an-mp3",
        ),
        pytest.param(
            "duration",
            "book.m4b",
            None,
            AudioObservationKind.DURATION_UNSTATED,
            id="headers-that-state-no-running-time",
        ),
        pytest.param(
            "encrypted",
            "book.m4b",
            None,
            AudioObservationKind.STREAM_ENCRYPTED,
            id="a-protected-sample-entry",
        ),
    ),
)
def test_one_departing_header_states_exactly_one_observation_and_no_claim(
    media_root: Path,
    case: str,
    name: str,
    written: None,
    kind: AudioObservationKind,
) -> None:
    """Each class, proved against real bytes through the real probe.

    Headers only: no sample is decoded for any of these, and each container
    differs from the healthy one in a single field. The assertion is deliberately
    exact -- one kind and no other -- because a class that also fired a second
    observation would tell an owner two things where one is true.

    Nothing here becomes a claim. A container this build did not expect is a
    statement about the file, so an unexpected codec must not reach the
    evidence set as a fact about the work, and an encrypted stream must not
    silently drop the tags the container still carries.
    """
    bodies = {
        "codec": lambda: mp4_audiobook(
            replace(_HEALTHY_FIXTURE, object_type=AC3_OBJECT_TYPE)
        ),
        "container": lambda: mp3_audiobook(
            seconds=4.0, frames={"TIT2": "Recorded Title"}
        ),
        "duration": lambda: mp4_audiobook(
            replace(_HEALTHY_FIXTURE, duration_stated=False)
        ),
        "encrypted": lambda: mp4_audiobook(
            replace(_HEALTHY_FIXTURE, sample_entry="drms")
        ),
    }
    (media_root / name).write_bytes(bodies[case]())

    result = _inspect(media_root, name)

    assert _observed_kinds(result) == {kind}
    assert [value.tracks for value in result.audio.observations] == [(1,)]
    assert all(value.candidate is None for value in result.audio.observations)
    assert result.claims, "the departing container still yields its ordinary claims"
    assert all(
        value.kind
        in {
            ClaimKind.ASIN,
            ClaimKind.DURATION,
            ClaimKind.LANGUAGE,
            ClaimKind.TITLE,
        }
        for value in result.claims
    )


def test_a_container_stating_no_running_time_is_not_also_called_unavailable(
    media_root: Path,
) -> None:
    """The precedence rule, proved on the bytes that raise both questions.

    A file whose headers state no running time composes no `DURATION` claim
    either, so the older `track_duration_unavailable` kind is true of it as
    well. Telling an owner both would be telling them one thing twice in two
    vocabularies, so the kind that names the cause is the one stated.
    """
    (media_root / "book.m4b").write_bytes(
        mp4_audiobook(replace(_HEALTHY_FIXTURE, duration_stated=False))
    )

    result = _inspect(media_root, "book.m4b")

    assert _observed_kinds(result) == {AudioObservationKind.DURATION_UNSTATED}
    assert AudioObservationKind.TRACK_DURATION_UNAVAILABLE not in _observed_kinds(
        result
    )
    assert all(value.kind is not ClaimKind.DURATION for value in result.claims)


def test_chapter_runtime_publication_fixture_matches_actual_probe(
    media_root: Path,
) -> None:
    from tests.support.chapter_runtime import ASIN, END, SPANS, media

    (media_root / "book.m4b").write_bytes(media())
    result = _inspect(media_root, "book.m4b")
    assert [
        (c.start_milliseconds, c.end_milliseconds) for c in result.audio.chapters
    ] == list(SPANS)
    assert any(c.kind is ClaimKind.ASIN and c.canonical == ASIN for c in result.claims)
    assert any(
        c.kind is ClaimKind.DURATION and c.canonical == f"{END / 1000:.3f}"
        for c in result.claims
    )
    assert result.audio.chapter_rows_omitted is False
    assert result.audio.chapters_truncated is False
    assert result.audio.composition is AudioChapterComposition.SINGLE_FILE


@pytest.mark.parametrize(
    "stated,chapter_stated", ((True, True), (False, True), (False, False))
)
def test_quicktime_chapters_keep_complete_missing_runtime_provenance(
    media_root: Path, stated: bool, chapter_stated: bool
) -> None:
    from mechanical_librarian.domain.audio_inspection import AudioObservationKind
    from mechanical_librarian.domain.audiobook_corroboration import (
        complete_chapter_runtime_agreement,
    )
    from mechanical_librarian.domain.metadata_report import ProviderChapterStructure
    from tests.support.chapter_runtime import ASIN, QT_SPANS, qt_media

    (media_root / "book.m4b").write_bytes(
        qt_media(stated=stated, chapter_stated=chapter_stated, asin=ASIN)
    )
    result = _inspect(media_root, "book.m4b")
    assert any(c.kind is ClaimKind.ASIN and c.canonical == ASIN for c in result.claims)
    assert result.audio.composition is AudioChapterComposition.SINGLE_FILE
    assert not result.audio.chapter_rows_omitted
    assert not result.audio.chapters_truncated
    if chapter_stated:
        assert [
            (c.start_milliseconds, c.end_milliseconds) for c in result.audio.chapters
        ] == list(QT_SPANS)
    else:
        assert (
            result.audio.chapters[-1].end_milliseconds
            == result.audio.chapters[-1].start_milliseconds
        )
    durations = tuple(c for c in result.claims if c.kind is ClaimKind.DURATION)
    assert bool(durations) is stated
    if not stated:
        assert result.audio.tracks[0].duration_milliseconds is None
        absence = tuple(
            o
            for o in result.audio.observations
            if o.kind is AudioObservationKind.DURATION_UNSTATED
        )
        assert len(absence) == 1 and absence[0].tracks == (1,)
        assert not any(
            o.kind is AudioObservationKind.TRACK_DURATION_UNAVAILABLE
            for o in result.audio.observations
        )
    structure = ProviderChapterStructure(
        6, 1748, tuple((end - start) // 1000 for start, end in QT_SPANS)
    )
    assert (
        complete_chapter_runtime_agreement(result.audio, result.claims, structure)
        is chapter_stated
    )
