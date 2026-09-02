"""The item-level budget and provenance the composed audiobook inspector owns.

These drive the real ``_IsolatedAudioInspector`` against real files on a real
mount with the isolated child replaced, so the parent's own counting, byte,
clock, and provenance decisions are proved without a probe binary present.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioItemInspectionError,
    TargetFile,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.bootstrap.composition import _IsolatedAudioInspector
from mechanical_librarian.domain.audio_inspection import (
    MAX_AUDIO_TRACKS,
    MAX_FACTS,
    MULTI_FILE_WALL_SECONDS,
    AudioChapterComposition,
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionFact,
    AudioInspectionField,
    AudioInspectionOutcome,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    local_claim_runtime_seconds,
)
from mechanical_librarian.domain.identifiers import Fingerprint
from mechanical_librarian.domain.metadata_report import canonical_json

_MAXIMUM = 1_048_576


class _RecordedChild:
    """Stand in for the two isolated children, recording what each track got."""

    def __init__(
        self,
        *,
        facts: Callable[[int, Fingerprint], tuple[AudioInspectionFact, ...]]
        | None = None,
        outcomes: dict[int, AudioInspectionOutcome] | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._facts = facts
        self._outcomes = outcomes or {}

    def __call__(
        self,
        descriptor: int,
        expected_source: Fingerprint,
        limits: object,
        *,
        sidecars: tuple[object, ...] = (),
        path_file: str = "",
        path_folder: str | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> AudioInspection:
        ordinal = len(self.calls) + 1
        self.calls.append(
            {
                "sidecars": sidecars,
                "path_file": path_file,
                "path_folder": path_folder,
                "cancelled": cancelled(),
            }
        )
        outcome = self._outcomes.get(ordinal, AudioInspectionOutcome.SUCCESS)
        if not outcome.succeeded:
            return AudioInspection(outcome, None)
        return AudioInspection(
            outcome,
            expected_source,
            (
                _duration_fact(expected_source)
                if self._facts is None
                else self._facts(ordinal, expected_source)
            ),
        )


def _duration_fact(source: Fingerprint) -> tuple[AudioInspectionFact, ...]:
    return (
        AudioInspectionFact(
            AudioInspectionField.DURATION,
            source,
            "1.000",
            "1.000",
            "1.000",
            AudioEvidenceSurface.CONTAINER_FORMAT,
        ),
    )


@pytest.fixture
def mount(tmp_path: Path) -> Iterator[MediaMountConfiguration]:
    yield MediaMountConfiguration("audiobooks", str(tmp_path), "/library", True)


def _write(mount: MediaMountConfiguration, name: str, payload: bytes) -> TargetFile:
    (Path(mount.container_path) / name).write_bytes(payload)
    return TargetFile(hashlib.sha256(name.encode()).hexdigest(), name, "mp3", len(name))


def _tracks(mount: MediaMountConfiguration, count: int) -> tuple[TargetFile, ...]:
    return tuple(
        _write(mount, f"{ordinal:02d}.mp3", f"track-{ordinal}".encode())
        for ordinal in range(1, count + 1)
    )


def test_a_set_past_the_track_bound_is_refused_before_any_file_is_opened(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = _RecordedChild()
    monkeypatch.setattr(composition, "run_audio_inspector_child", child)
    absent = tuple(
        TargetFile(f"{index:064x}", f"{index}.mp3", "mp3")
        for index in range(MAX_AUDIO_TRACKS + 1)
    )

    with pytest.raises(AudioItemInspectionError) as raised:
        _IsolatedAudioInspector().inspect(mount, absent, _MAXIMUM)

    assert raised.value.outcome is AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
    assert child.calls == []


def test_a_track_past_the_item_byte_ceiling_is_a_budget_terminal(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multi-file item's bytes are one budget, so the refusal names it.

    A one-track item keeps F43's own per-file refusal, which is a parser
    limit rather than an item budget.
    """
    monkeypatch.setattr(composition, "run_audio_inspector_child", _RecordedChild())
    tracks = (
        _write(mount, "01.mp3", b"small"),
        _write(mount, "02.mp3", b"x" * 64),
    )

    with pytest.raises(AudioItemInspectionError) as raised:
        _IsolatedAudioInspector().inspect(mount, tracks, 32)

    assert raised.value.outcome is AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED


def test_the_item_wall_stops_a_wide_set_and_names_the_budget(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(composition, "run_audio_inspector_child", _RecordedChild())
    readings = iter((0.0, 0.0, MULTI_FILE_WALL_SECONDS + 1.0, 10_000.0))
    tracks = _tracks(mount, 3)

    with pytest.raises(AudioItemInspectionError) as raised:
        _IsolatedAudioInspector(lambda: next(readings)).inspect(mount, tracks, _MAXIMUM)

    assert raised.value.outcome is AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED


def test_a_single_track_item_never_arms_the_item_clock(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One file was already bounded by the per-file wall F43 installed."""
    child = _RecordedChild()
    monkeypatch.setattr(composition, "run_audio_inspector_child", child)
    readings = iter((0.0, 10_000.0, 10_000.0, 10_000.0))

    result = _IsolatedAudioInspector(lambda: next(readings)).inspect(
        mount, _tracks(mount, 1), _MAXIMUM
    )

    assert result.audio.composition is AudioChapterComposition.SINGLE_FILE
    assert child.calls[0]["cancelled"] is False


def test_one_unreadable_track_ends_the_item_under_that_track_s_own_outcome(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An item is one evidence set: a refused track publishes none of it."""
    monkeypatch.setattr(
        composition,
        "run_audio_inspector_child",
        _RecordedChild(outcomes={2: AudioInspectionOutcome.MALFORMED}),
    )

    with pytest.raises(AudioItemInspectionError) as raised:
        _IsolatedAudioInspector().inspect(mount, _tracks(mount, 3), _MAXIMUM)

    assert raised.value.outcome is AudioInspectionOutcome.MALFORMED
    assert raised.value.track == 2


def test_folder_evidence_reaches_only_the_first_track_s_child(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One folder is one surface, read once.

    Handing the sidecars to every child would multiply one document's evidence
    by the number of files beside it, and attribute each copy to a track that
    never held it.
    """
    child = _RecordedChild()
    monkeypatch.setattr(composition, "run_audio_inspector_child", child)
    (Path(mount.container_path) / "metadata.json").write_text("{}", encoding="utf-8")

    _IsolatedAudioInspector().inspect(mount, _tracks(mount, 3), _MAXIMUM)

    assert [bool(call["sidecars"]) for call in child.calls] == [True, False, False]
    assert [call["path_file"] for call in child.calls] == [
        "01.mp3",
        "02.mp3",
        "03.mp3",
    ]


def test_the_item_digest_composes_every_track_digest_in_manifest_order(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(composition, "run_audio_inspector_child", _RecordedChild())

    result = _IsolatedAudioInspector().inspect(mount, _tracks(mount, 3), _MAXIMUM)

    digests = [value.digest for value in result.audio.tracks]
    assert digests == [
        hashlib.sha256(f"track-{ordinal}".encode()).hexdigest() for ordinal in (1, 2, 3)
    ]
    assert (
        result.content_digest
        == hashlib.sha256(canonical_json(digests).encode("utf-8")).hexdigest()
    )
    assert [value.ordinal for value in result.audio.tracks] == [1, 2, 3]
    assert [value.sequence for value in result.audio.tracks] == [6, 6, 6]


def test_every_composed_claim_resolves_under_the_tracks_that_carried_it(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    def facts(ordinal: int, source: Fingerprint) -> tuple[AudioInspectionFact, ...]:
        return (
            *_duration_fact(source),
            AudioInspectionFact(
                AudioInspectionField.SERIES,
                source,
                "Discworld" if ordinal < 3 else "Elsewhere",
                "Discworld" if ordinal < 3 else "Elsewhere",
                f"series: {'Discworld' if ordinal < 3 else 'Elsewhere'}",
                AudioEvidenceSurface.ID3_USER_TEXT,
            ),
        )

    monkeypatch.setattr(
        composition, "run_audio_inspector_child", _RecordedChild(facts=facts)
    )

    result = _IsolatedAudioInspector().inspect(mount, _tracks(mount, 3), _MAXIMUM)

    references = {claim.reference for claim in result.claims}
    assert all(
        reference in references
        for track in result.audio.tracks
        for reference in track.claims
    )
    shared = set(result.audio.tracks[0].claims) & set(result.audio.tracks[1].claims)
    assert shared and not shared & set(result.audio.tracks[2].claims)


def test_a_composed_claim_set_past_its_ceiling_is_a_budget_terminal(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half an item's evidence is not the item's evidence set."""

    def facts(ordinal: int, source: Fingerprint) -> tuple[AudioInspectionFact, ...]:
        return tuple(
            AudioInspectionFact(
                AudioInspectionField.SERIES,
                source,
                f"Series {ordinal}-{index}",
                f"Series {ordinal}-{index}",
                f"Series {ordinal}-{index}",
                AudioEvidenceSurface.ID3_USER_TEXT,
            )
            for index in range(MAX_FACTS // 2)
        )

    monkeypatch.setattr(
        composition, "run_audio_inspector_child", _RecordedChild(facts=facts)
    )

    with pytest.raises(AudioItemInspectionError) as raised:
        _IsolatedAudioInspector().inspect(mount, _tracks(mount, 3), _MAXIMUM)

    assert raised.value.outcome is AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED


def test_the_composed_item_gives_the_runtime_gate_one_value_to_elect(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider path reads composed claims with no change of its own.

    Its runtime gate elects a single agreed local value, so a set publishing
    one duration per file would have corroborated nothing at all.
    """

    def facts(ordinal: int, source: Fingerprint) -> tuple[AudioInspectionFact, ...]:
        observed = f"{ordinal * 60}.000"
        return (
            AudioInspectionFact(
                AudioInspectionField.DURATION,
                source,
                observed,
                observed,
                observed,
                AudioEvidenceSurface.CONTAINER_FORMAT,
            ),
        )

    monkeypatch.setattr(
        composition, "run_audio_inspector_child", _RecordedChild(facts=facts)
    )

    result = _IsolatedAudioInspector().inspect(mount, _tracks(mount, 3), _MAXIMUM)

    assert local_claim_runtime_seconds(result.claims) == 360


def test_currentness_re_digests_every_track_and_notices_one_edit(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(composition, "run_audio_inspector_child", _RecordedChild())
    tracks = _tracks(mount, 3)
    inspector = _IsolatedAudioInspector()
    result = inspector.inspect(mount, tracks, _MAXIMUM)
    paths = tuple(value.relative_path for value in tracks)

    assert inspector.unchanged(
        mount, paths, result.content_digest, _MAXIMUM, (), result.audio.tracks
    )

    (Path(mount.container_path) / "02.mp3").write_bytes(b"rewritten")
    assert not inspector.unchanged(
        mount, paths, result.content_digest, _MAXIMUM, (), result.audio.tracks
    )


def test_currentness_refuses_a_set_that_lost_a_track(
    mount: MediaMountConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(composition, "run_audio_inspector_child", _RecordedChild())
    tracks = _tracks(mount, 3)
    inspector = _IsolatedAudioInspector()
    result = inspector.inspect(mount, tracks, _MAXIMUM)

    assert not inspector.unchanged(
        mount,
        tuple(value.relative_path for value in tracks[:2]),
        result.content_digest,
        _MAXIMUM,
        (),
        result.audio.tracks,
    )
