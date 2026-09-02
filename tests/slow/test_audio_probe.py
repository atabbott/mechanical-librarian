"""The composed audiobook inspector over a real probe binary and real media.

This is the one cohort that executes ffprobe. It builds each container in
code, writes it into a temporary mount, and drives the same
``_IsolatedAudioInspector`` the application composes, so the probe child, the
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
from pathlib import Path

import pytest

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
from mechanical_librarian.bootstrap.composition import _IsolatedAudioInspector
from mechanical_librarian.domain.audio_inspection import (
    AudioChapterComposition,
    AudioInspectionOutcome,
    AudioObservationKind,
)
from mechanical_librarian.domain.metadata_report import ClaimKind
from tests.support.audio_containers import (
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
    return _IsolatedAudioInspector().inspect(
        _mount(root), (_track(relative_path),), _MAXIMUM_BYTES
    )


def _inspect_set(root: Path, names: tuple[str, ...]) -> AudioInspectionResult:
    return _IsolatedAudioInspector().inspect(
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
