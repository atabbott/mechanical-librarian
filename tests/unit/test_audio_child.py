"""Characterization of the isolated audio child and its parent protocol.

The probe binary is never required here: every case either drives the Python
child directly or hands the parent a recorded probe document, so the whole
protocol is proved without ffmpeg installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import mechanical_librarian.bootstrap.audio_child as child
from mechanical_librarian.adapters.evidence_extractors import process
from mechanical_librarian.adapters.evidence_extractors.process import (
    resolve_probe_path,
    run_audio_inspector_child,
    run_probe_child,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_CHAPTER_TITLE_CHARACTERS,
    MAX_CHAPTERS,
    MAX_PUBLIC_REPORT_BYTES,
    AudioChapter,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionField,
    AudioInspectionLimits,
    AudioInspectionOutcome,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion

_DIGEST = hashlib.sha256(b"audiobook").hexdigest()
_SOURCE = Fingerprint("sha256", SchemaVersion("local-file-v1"), _DIGEST)
_PROBE = json.dumps(
    {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "4.000",
            "tags": {"CDEK": "B0CW1KL2QD", "narrator": "Recorded Narrator"},
        },
        "streams": [{"codec_type": "audio", "codec_name": "aac"}],
        "chapters": [{"start_time": "0.000", "end_time": "4.000"}],
    }
)


def _context(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "capability": "audio",
        "child_seconds": 4.5,
        "expected_source": {
            "algorithm": "sha256",
            "digest": _DIGEST,
            "schema": "local-file-v1",
        },
        "limits": AudioInspectionLimits().to_data(),
        "path": {"file": "book.m4b", "folder": None},
        "probe": _PROBE,
        "sidecars": [],
    }
    value.update(overrides)
    return value


def _run(context: dict[str, object]) -> list[AudioInspection]:
    written: list[AudioInspection] = []
    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", return_value=context),
        patch.object(child, "_write_report", side_effect=written.append),
        patch.object(child.signal, "signal"),
        patch.object(child.signal, "setitimer"),
    ):
        assert child.main(["10", "123"]) == 0
    return written


def test_a_complete_context_produces_one_attributed_inspection() -> None:
    written = _run(_context())

    assert len(written) == 1
    report = written[0]
    assert report.outcome is AudioInspectionOutcome.SUCCESS
    assert report.source == _SOURCE
    assert {value.field for value in report.facts} == {
        AudioInspectionField.ASIN,
        AudioInspectionField.CONTRIBUTOR,
        AudioInspectionField.DURATION,
    }
    assert [value.ordinal for value in report.chapters] == [1]


def test_a_readable_container_without_evidence_still_carries_its_source() -> None:
    written = _run(
        _context(
            probe=json.dumps(
                {
                    "format": {"format_name": "mp3"},
                    "streams": [{"codec_type": "audio"}],
                    "chapters": [],
                }
            )
        )
    )

    assert written[0].outcome is AudioInspectionOutcome.NO_APPLICABLE_EVIDENCE
    assert written[0].source == _SOURCE


def test_an_unreadable_probe_retains_no_source_and_no_evidence() -> None:
    written = _run(_context(probe="not a document"))

    assert written[0].outcome is AudioInspectionOutcome.MALFORMED
    assert written[0].source is None
    assert written[0].facts == ()


@pytest.mark.parametrize(
    "overrides",
    (
        {"capability": "opening"},
        {"child_seconds": 0},
        {"child_seconds": 600.0},
        {"probe": 17},
        {"path": {"file": 3, "folder": None}},
        {"path": {"file": "book.m4b"}},
        {"expected_source": {"algorithm": "sha256", "digest": _DIGEST}},
        {"sidecars": "not-a-list"},
    ),
)
def test_a_context_outside_the_closed_protocol_is_an_internal_failure(
    overrides: dict[str, object],
) -> None:
    written = _run(_context(**overrides))

    assert written[0].outcome is AudioInspectionOutcome.INTERNAL_FAILURE
    assert written[0].source is None


def test_a_probe_larger_than_the_configured_ceiling_is_refused() -> None:
    limits = AudioInspectionLimits(probe_output_bytes=len(_PROBE) - 1)

    written = _run(_context(limits=limits.to_data()))

    assert written[0].outcome is AudioInspectionOutcome.INTERNAL_FAILURE


def test_a_limit_document_from_another_profile_is_refused() -> None:
    limits = dict(AudioInspectionLimits().to_data())
    limits["profile"] = "audio-inspection-limits-v2"

    written = _run(_context(limits=limits))

    assert written[0].outcome is AudioInspectionOutcome.INTERNAL_FAILURE


def test_exhausted_memory_is_a_resource_limit_terminal() -> None:
    written: list[AudioInspection] = []
    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", side_effect=MemoryError),
        patch.object(child, "_write_report", side_effect=written.append),
        patch.object(child.signal, "signal"),
    ):
        assert child.main(["10", "123"]) == 0

    assert [value.outcome for value in written] == [
        AudioInspectionOutcome.RESOURCE_LIMIT
    ]


def test_a_context_key_outside_the_closed_protocol_is_refused() -> None:
    """The child reads exactly eight keys; a ninth is a protocol violation.

    `logical_identity` was one such key until nothing bound it: the expected
    source already names the payload the report is stamped with.
    """
    payload = json.dumps(_context(logical_identity=_DIGEST)).encode("utf-8")
    stream = SimpleNamespace(buffer=SimpleNamespace(read=lambda maximum: payload))

    with patch.object(child.sys, "stdin", stream):
        with pytest.raises(ValueError):
            child._read_context()


def test_the_child_holds_its_own_deadline_over_the_interpretation() -> None:
    """The child names its own timeout rather than waiting to be killed."""
    armed: list[tuple[int, float]] = []

    def arm(which: int, seconds: float) -> None:
        armed.append((which, seconds))

    written: list[AudioInspection] = []
    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", return_value=_context()),
        patch.object(child, "_write_report", side_effect=written.append),
        patch.object(child, "interpret", side_effect=child._Expired),
        patch.object(child.signal, "signal"),
        patch.object(child.signal, "setitimer", side_effect=arm),
    ):
        assert child.main(["10", "123"]) == 0

    assert written[0].outcome is AudioInspectionOutcome.TIMEOUT
    assert written[0].source is None
    assert armed[0] == (child.signal.ITIMER_REAL, 4.5)
    # Disarmed on the way out, whatever the outcome was.
    assert armed[-1] == (child.signal.ITIMER_REAL, 0)


def test_the_deadline_signal_raises_rather_than_ending_the_process() -> None:
    with pytest.raises(child._Expired):
        child._expire(child.signal.SIGALRM, None)


def test_a_context_larger_than_the_child_ceiling_is_refused_before_parsing() -> None:
    oversized = SimpleNamespace(
        buffer=SimpleNamespace(
            read=lambda maximum: (b"{}" + b" " * child._CONTEXT_LIMIT)[:maximum]
        )
    )

    with patch.object(child.sys, "stdin", oversized):
        with pytest.raises(ValueError):
            child._read_context()


def test_an_oversized_or_unreadable_sidecar_is_dropped_rather_than_truncated() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "book.nfo"
        path.write_text("Narrator: Recorded Narrator\n", encoding="utf-8")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            assert child._sidecar_text(descriptor, 4) is None
            assert child._sidecar_text(descriptor, 1024) == (
                "Narrator: Recorded Narrator\n"
            )
        finally:
            os.close(descriptor)
        assert child._sidecar_text(descriptor, 1024) is None


def test_sidecar_bytes_reach_the_interpretation_through_passed_descriptors() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "book.nfo"
        path.write_text("Narrator: Sidecar Narrator\n", encoding="utf-8")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            written = _run(
                _context(
                    sidecars=[
                        {
                            "descriptor": descriptor,
                            "name": "book.nfo",
                            "surface": AudioEvidenceSurface.SIDECAR_NFO.value,
                        }
                    ]
                )
            )
        finally:
            os.close(descriptor)

    assert ("sidecar_nfo", "Sidecar Narrator") in {
        (value.surface.value, value.observed_value) for value in written[0].facts
    }


def test_the_probe_child_keeps_no_process_cap_so_a_threaded_probe_can_run() -> None:
    applied: list[int] = []
    with (
        patch.object(
            process.resource,
            "getrlimit",
            return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
        ),
        patch.object(
            process.resource,
            "setrlimit",
            side_effect=lambda limit, value: applied.append(limit),
        ),
    ):
        process._probe_limits()

    assert resource.RLIMIT_NPROC not in applied
    assert {
        resource.RLIMIT_CORE,
        resource.RLIMIT_CPU,
        resource.RLIMIT_FSIZE,
        resource.RLIMIT_NOFILE,
    } <= set(applied)


def test_the_interpretation_child_caps_its_own_process_count_on_linux() -> None:
    applied: list[tuple[int, tuple[int, int]]] = []
    with (
        patch.object(child.sys, "platform", "linux"),
        patch.object(
            child.resource,
            "getrlimit",
            return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
        ),
        patch.object(
            child.resource,
            "setrlimit",
            side_effect=lambda limit, value: applied.append((limit, value)),
        ),
    ):
        child._apply_limits()

    assert (resource.RLIMIT_NPROC, (1, 1)) in applied


def test_a_missing_probe_binary_is_a_configuration_outcome() -> None:
    with patch.object(process, "resolve_probe_path", return_value=None):
        payload, outcome = run_probe_child(0, AudioInspectionLimits())

    assert payload is None
    assert outcome is AudioInspectionOutcome.INVALID_CONFIGURATION


def test_the_probe_binary_is_resolved_from_a_closed_list_not_from_path() -> None:
    with patch.object(process.os.path, "isfile", return_value=False):
        assert resolve_probe_path() is None
    assert all(value.startswith("/") for value in process._PROBE_PATHS)


def test_limits_of_the_wrong_type_never_start_a_child() -> None:
    with patch.object(process, "run_probe_child") as probe:
        report = run_audio_inspector_child(0, _SOURCE, object())  # type: ignore[arg-type]

    probe.assert_not_called()
    assert report.outcome is AudioInspectionOutcome.INTERNAL_FAILURE


def test_the_parent_runs_the_real_child_over_a_recorded_probe_document() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        media = Path(temporary) / "book.m4b"
        media.write_bytes(b"unused: the probe document is supplied")
        sidecar = Path(temporary) / "book.nfo"
        sidecar.write_text("Series: Discworld\n", encoding="utf-8")
        media_descriptor = os.open(media, os.O_RDONLY)
        sidecar_descriptor = os.open(sidecar, os.O_RDONLY)
        try:
            with patch.object(
                process,
                "run_probe_child",
                return_value=(_PROBE.encode(), AudioInspectionOutcome.SUCCESS),
            ):
                report = run_audio_inspector_child(
                    media_descriptor,
                    _SOURCE,
                    AudioInspectionLimits(),
                    sidecars=(
                        (
                            AudioEvidenceSurface.SIDECAR_NFO.value,
                            "book.nfo",
                            sidecar_descriptor,
                        ),
                    ),
                    path_file="book [B0CW1KL2QD].m4b",
                )
        finally:
            os.close(media_descriptor)
            os.close(sidecar_descriptor)

    assert report.outcome is AudioInspectionOutcome.SUCCESS
    assert report.source == _SOURCE
    assert {
        (value.field.value, value.surface.value, value.observed_value)
        for value in report.facts
    } >= {
        ("asin", "mp4_ilst", "B0CW1KL2QD"),
        ("asin", "path_file", "B0CW1KL2QD"),
        ("series", "sidecar_nfo", "Discworld"),
        ("duration", "container_format", "4.000"),
    }


def test_a_probe_failure_stops_the_capability_before_the_second_child() -> None:
    with patch.object(
        process,
        "run_probe_child",
        return_value=(None, AudioInspectionOutcome.PROBE_FAILURE),
    ):
        report = run_audio_inspector_child(0, _SOURCE, AudioInspectionLimits())

    assert report.outcome is AudioInspectionOutcome.PROBE_FAILURE
    assert report.source is None
    assert report.facts == ()


def test_the_parent_guard_exits_a_child_that_was_already_reparented() -> None:
    with (
        patch.object(child.sys, "platform", "linux"),
        patch.object(child.os, "getppid", return_value=1),
        patch.object(child.os, "_exit", side_effect=SystemExit(70)) as exit_process,
    ):
        with pytest.raises(SystemExit):
            child._install_parent_guard(10, threading.Event(), 999)

    exit_process.assert_called_once_with(70)


def test_a_maximal_legal_inspection_survives_its_own_protocol() -> None:
    """The ceiling has to hold the largest report this vocabulary admits.

    A file may legally carry 1,024 chapters, each titled to the full width, in
    characters that cost four bytes each.  At 512 KB the protocol refused its
    own output, and the whole inspection was lost to a child failure.
    """
    wide = "\U0001d11e"
    chapters = tuple(
        AudioChapter(index + 1, index, index + 1, wide * MAX_CHAPTER_TITLE_CHARACTERS)
        for index in range(MAX_CHAPTERS)
    )
    report = AudioInspection(
        AudioInspectionOutcome.SUCCESS,
        _SOURCE,
        (),
        chapters,
        AudioEmbeddedCover(True, AudioEvidenceSurface.CONTAINER_STREAM),
        True,
    )
    serialized = report.to_json()

    assert len(serialized.encode("utf-8")) > 1_048_576
    assert len(serialized.encode("utf-8")) <= MAX_PUBLIC_REPORT_BYTES
    assert AudioInspection.from_json(serialized) == report
    # And the child may write exactly what the protocol accepts.
    assert len((serialized + "\n").encode("utf-8")) <= child._STDOUT_LIMIT
