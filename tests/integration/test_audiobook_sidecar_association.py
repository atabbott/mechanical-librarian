"""An audiobook's sidecars, from the folder to the published report (#1481).

The issue's demonstration, against the real composition seam: the parent
inspector is the real one and opens real files in a temporary mount; a
stand-in child applies the real decoder and grammar (no ffprobe); the target
reader and the providers are the activation suite's fakes.  Every title, name
and identifier here is synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.evidence_extractors import isolated_audio
from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    MetadataProvider,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    MetadataReport,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.integration import test_audiobook_provider_activation as activation
from tests.support.audio_grammar_child import GrammarChild

_TITLE = "Ember Tide Protocol"
_AUTHOR = "Corin Halloway"
_RECORD_ASIN = "B0SYNREC01"
_SIDECAR_ASIN = activation._ASIN
_FOLDER = "Item"
_TRACK = f"{_FOLDER}/book.m4b"
_MAXIMUM = 268_435_456


def _mount(root: Path) -> MediaMountConfiguration:
    return MediaMountConfiguration("audiobooks", str(root / "media"), "/service", True)


def _fields(asin: str | None) -> tuple[TargetField, ...]:
    fields = activation._target_fields()
    if asin is None:
        return fields
    return tuple(
        TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, asin)
        if field.destination is TargetDestination.ASIN
        else field
        for field in fields
    )


def _snapshot(own: tuple[str, ...], asin: str | None) -> TargetSnapshot:
    return TargetSnapshot(
        "audiobook",
        _TITLE,
        (TargetFile(activation._FILE_IDENTITY, _TRACK, "m4b"),),
        fields=_fields(asin),
        own_file_names=own,
    )


def _library(root: Path, documents: dict[str, str]) -> tuple[str, ...]:
    folder = root / "media" / _FOLDER
    folder.mkdir(parents=True)
    (folder / "book.m4b").write_bytes(b"synthetic recording")
    for name, text in documents.items():
        (folder / name).write_text(text, encoding="utf-8")
    return ("book.m4b", *documents)


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch) -> Iterator[GrammarChild]:
    stand_in = GrammarChild()
    monkeypatch.setattr(isolated_audio, "run_audio_inspector_child", stand_in)
    yield stand_in


def _publish(
    root: Path, own: tuple[str, ...], *, asin: str | None = None
) -> tuple[MetadataReport, list[tuple[str, ProviderQueryKind]], AudioInspectionResult]:
    """Inspect the real folder and publish the report through `_run`."""
    snapshot = _snapshot(own, asin)
    inspected = IsolatedAudioInspector().inspect(
        _mount(root),
        snapshot.files,
        _MAXIMUM,
        association=AudioAssociationContext(_TITLE, snapshot.fields, own),
    )
    calls: list[tuple[str, ProviderQueryKind]] = []
    providers: dict[str, MetadataProvider | Callable[[], MetadataProvider]] = {
        "audible_catalog": activation._FakeAudiobookProvider(
            "audible", "audible_catalog", calls, status=ProviderStatus.NO_MATCH
        ),
        "itunes_search": activation._FakeAudiobookProvider(
            "store", "itunes_search", calls, status=ProviderStatus.NO_MATCH
        ),
        "audnexus": activation._FakeAudiobookProvider(
            "enrichment", "audnexus", calls, status=ProviderStatus.NO_MATCH
        ),
        "audiosilo": activation._FakeAudiobookProvider(
            "silo", "audiosilo", calls, status=ProviderStatus.NO_MATCH
        ),
    }

    class _Inspector:
        """Hand back the real inspection; prove currentness on the real folder."""

        def __init__(self, *_args: object, **_keywords: object) -> None:
            pass

        def inspect(self, *_args: object, **_keywords: object) -> AudioInspectionResult:
            return inspected

        def unchanged(
            self,
            _mount_configuration: object,
            relative_paths: tuple[str, ...],
            expected_digest: str,
            maximum_bytes: int,
            expected_sidecars: tuple[object, ...] = (),
            expected_tracks: tuple[object, ...] = (),
            *,
            own_file_names: tuple[str, ...] | None = None,
        ) -> bool:
            return IsolatedAudioInspector().unchanged(
                _mount(root),
                relative_paths,
                expected_digest,
                maximum_bytes,
                expected_sidecars,  # type: ignore[arg-type]
                expected_tracks,  # type: ignore[arg-type]
                own_file_names=own_file_names,
            )

    with (
        patch.object(activation, "_AudioInspector", _Inspector),
        patch.object(activation, "_snapshot", return_value=snapshot),
    ):
        serialized, repository = activation._run(root, providers)
    repository.close()
    return MetadataReport.from_json(serialized), calls, inspected


def _asin_lane_references(report: MetadataReport) -> set[str]:
    return {
        reference
        for provider in report.providers
        for lane in provider.lanes
        if lane.kind is ProviderQueryKind.ASIN
        for reference in lane.input_references
    }


def _claims(report: MetadataReport, basis: str) -> list[tuple[str, str]]:
    return [
        (claim.kind.value, str(claim.canonical))
        for claim in report.local_claims
        if claim.basis == basis
    ]


def test_audiobookshelfs_own_record_yields_no_evidence_and_opens_no_lane(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I1: the record is present and refused; the target carries its values."""
    record = json.dumps(
        {
            "asin": _RECORD_ASIN,
            "title": _TITLE,
            "authors": [_AUTHOR],
            "series": ["Synthetic Series #3"],
        }
    )
    own = _library(tmp_path, {"metadata.json": record})

    report, _, _ = _publish(tmp_path, own, asin=_RECORD_ASIN)

    assert child.handed == [()]
    assert _claims(report, "sidecar") == []
    assert report.audio is not None
    [entry] = report.audio.sidecars
    assert (entry.kind.value, [value.value for value in entry.refusals]) == (
        "audiobookshelf_record",
        ["target_record"],
    )
    asserted = {
        claim.reference
        for claim in report.local_claims
        if claim.kind is ClaimKind.ASIN and claim.canonical == _RECORD_ASIN
    }
    assert asserted
    assert all(
        claim.basis != "sidecar"
        for claim in report.local_claims
        if claim.reference in asserted
    )
    sidecar_references = {
        claim.reference for claim in report.local_claims if claim.basis == "sidecar"
    }
    assert not _asin_lane_references(report) & sidecar_references


def test_a_sidecar_copied_from_another_book_contributes_nothing(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I2: the manifest records `other_book`, and no claim comes from it."""
    copied = (
        "<package><metadata>"
        "<dc:title>A Different Synthetic Book</dc:title>"
        "<dc:creator>Another Synthetic Author</dc:creator>"
        f'<meta name="calibre:asin" content="{_SIDECAR_ASIN}"/>'
        "</metadata></package>"
    )
    own = _library(tmp_path, {"book.opf": copied})

    report, _, _ = _publish(tmp_path, own)

    assert _claims(report, "sidecar") == []
    assert report.audio is not None
    [entry] = report.audio.sidecars
    assert [value.value for value in entry.refusals] == ["other_book"]
    assert entry.claims == ()


def test_a_libation_export_for_the_track_supplies_its_asin_and_opens_its_lane(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I3: the per-file export is the track's, and its ASIN is evidence."""
    export = json.dumps(
        {
            "asin": _SIDECAR_ASIN,
            "title": _TITLE,
            "authors": [{"name": _AUTHOR}],
            "narrators": [{"name": "Example Narrator"}],
        }
    )
    own = _library(tmp_path, {"book.metadata.json": export})

    report, calls, _ = _publish(tmp_path, own)

    assert ("asin", _SIDECAR_ASIN) in _claims(report, "sidecar")
    [reference] = [
        claim.reference
        for claim in report.local_claims
        if claim.basis == "sidecar" and claim.kind is ClaimKind.ASIN
    ]
    assert report.audio is not None
    [entry] = report.audio.sidecars
    assert (entry.binding.value, entry.refusals) == ("track", ())
    assert reference in entry.claims
    assert reference in _asin_lane_references(report)
    assert ("audible_catalog", ProviderQueryKind.ASIN) in calls


def test_a_foreign_file_added_to_the_folder_makes_the_report_stale(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I4: currentness recomputes the shared folder from the listing."""
    folder_document = (
        "<package><metadata>"
        f"<dc:title>{_TITLE}</dc:title><dc:creator>{_AUTHOR}</dc:creator>"
        "<dc:language>English</dc:language>"
        "</metadata></package>"
    )
    own = _library(tmp_path, {"folder.opf": folder_document})
    report, _, inspected = _publish(tmp_path, own)
    assert report.audio is not None

    def current() -> bool:
        return IsolatedAudioInspector().unchanged(
            _mount(tmp_path),
            (_TRACK,),
            inspected.content_digest,
            _MAXIMUM,
            report.audio.sidecars,  # type: ignore[union-attr]
            report.audio.tracks,  # type: ignore[union-attr]
            own_file_names=own,
        )

    assert [value.refusals for value in report.audio.sidecars] == [()]
    assert current()
    (tmp_path / "media" / _FOLDER / "other.flac").write_bytes(b"another item")
    assert not current()
