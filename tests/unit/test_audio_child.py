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
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import mechanical_librarian.bootstrap.audio_child as child
import mechanical_librarian.bootstrap.probe_shim as shim
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


def test_the_deadline_is_lifted_before_a_complete_report_is_written() -> None:
    """An alarm during the write would turn a whole report into a failure."""
    events: list[str] = []
    written: list[AudioInspection] = []

    def record(value: AudioInspection) -> None:
        events.append("write")
        written.append(value)

    with (
        patch.object(child, "_install_parent_guard"),
        patch.object(child, "_apply_limits"),
        patch.object(child, "_read_context", return_value=_context()),
        patch.object(child, "_write_report", side_effect=record),
        patch.object(child.signal, "signal"),
        patch.object(
            child.signal,
            "setitimer",
            side_effect=lambda _which, seconds: events.append(
                "arm" if seconds else "disarm"
            ),
        ),
    ):
        assert child.main(["10", "123"]) == 0

    assert written[0].outcome is AudioInspectionOutcome.SUCCESS
    assert events == ["arm", "disarm", "write", "disarm"]


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


def test_bytes_that_are_not_utf_8_drop_the_sidecar_rather_than_replacing_them() -> None:
    """A U+FFFD the owner's file never held is not evidence about their file."""
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "book.nfo"
        path.write_bytes(b"Narrator: Recorded \xff\xfe Narrator\n")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            assert child._sidecar_text(descriptor, 1024) is None
        finally:
            os.close(descriptor)


def test_a_replacement_character_the_source_really_holds_is_still_read() -> None:
    """Strict decoding drops undecodable bytes, not a legitimate character."""
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "book.nfo"
        path.write_text("Narrator: Recorded \ufffd Narrator\n", encoding="utf-8")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            assert child._sidecar_text(descriptor, 1024) == (
                "Narrator: Recorded \ufffd Narrator\n"
            )
        finally:
            os.close(descriptor)


def test_one_undecodable_sidecar_never_stops_the_others() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        broken = Path(temporary) / "broken.nfo"
        broken.write_bytes(b"Series: \xff\xfe\n")
        sound = Path(temporary) / "book.nfo"
        sound.write_text("Series: Discworld\n", encoding="utf-8")
        descriptors = [os.open(value, os.O_RDONLY) for value in (broken, sound)]
        try:
            written = _run(
                _context(
                    sidecars=[
                        {
                            "descriptor": descriptors[0],
                            "name": "broken.nfo",
                            "surface": AudioEvidenceSurface.SIDECAR_NFO.value,
                        },
                        {
                            "descriptor": descriptors[1],
                            "name": "book.nfo",
                            "surface": AudioEvidenceSurface.SIDECAR_NFO.value,
                        },
                    ]
                )
            )
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    assert {value.observed_value for value in written[0].facts} >= {"Discworld"}
    assert not any("\ufffd" in value.observed_value for value in written[0].facts)


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
            shim.resource,
            "getrlimit",
            return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
        ),
        patch.object(
            shim.resource,
            "setrlimit",
            side_effect=lambda limit, value: applied.append(limit),
        ),
    ):
        shim.apply_limits()

    assert resource.RLIMIT_NPROC not in applied
    assert {
        resource.RLIMIT_CORE,
        resource.RLIMIT_CPU,
        resource.RLIMIT_FSIZE,
        resource.RLIMIT_NOFILE,
    } <= set(applied)


def test_the_probe_address_space_limit_fails_closed_away_from_darwin() -> None:
    """Darwin's carve-out is a development convenience, never a production one."""

    def refuse(limit: int, _value: tuple[int, int]) -> None:
        if limit == resource.RLIMIT_AS:
            raise ValueError("address space limit refused")

    with (
        patch.object(shim.sys, "platform", "linux"),
        patch.object(
            shim.resource,
            "getrlimit",
            return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
        ),
        patch.object(shim.resource, "setrlimit", side_effect=refuse),
        pytest.raises(ValueError),
    ):
        shim.apply_limits()


def test_the_probe_is_never_bounded_between_a_fork_and_an_exec() -> None:
    """`preexec_fn` runs Python in a forked child holding a threaded parent's
    locks, and the probe is spawned from a threaded supervisor. The shim is
    what makes the bounded step an ordinary process instead."""
    source = Path(process.__file__).read_text(encoding="utf-8")

    assert "preexec_fn=" not in source
    assert "mechanical_librarian.bootstrap.probe_shim" in source


def test_the_shim_bounds_itself_and_then_becomes_the_binary_it_was_given() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        binary = Path(temporary) / "stub"
        binary.write_text(
            '#!/bin/sh\nulimit -c; ulimit -n; ulimit -t; echo "argv:$*"\n',
            encoding="utf-8",
        )
        binary.chmod(0o755)
        read, write = os.pipe()
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "mechanical_librarian.bootstrap.probe_shim",
                    str(read),
                    str(binary),
                    "-of",
                    "json",
                ],
                pass_fds=(read,),
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            os.close(read)
            os.close(write)

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == ["0", "64", "30", "argv:-of json"]


def test_the_shim_that_cannot_become_a_probe_exits_non_zero() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "mechanical_librarian.bootstrap.probe_shim",
            "0",
            "/nonexistent/ffprobe",
        ],
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode != 0


def test_the_descriptor_the_parent_passed_survives_the_shim_exec() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        binary = Path(temporary) / "stub"
        binary.write_text("#!/bin/sh\ncat /dev/fd/$1\n", encoding="utf-8")
        binary.chmod(0o755)
        media = Path(temporary) / "book.m4b"
        media.write_text("recorded bytes", encoding="utf-8")
        descriptor = os.open(media, os.O_RDONLY)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "mechanical_librarian.bootstrap.probe_shim",
                    str(descriptor),
                    str(binary),
                    str(descriptor),
                ],
                pass_fds=(descriptor,),
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            os.close(descriptor)

    assert completed.stdout == "recorded bytes"


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


@pytest.mark.parametrize("value", (None, 0, 1, "false"))
def test_child_protocol_requires_exact_bool_chapter_omission(value) -> None:
    document = json.loads(
        AudioInspection(AudioInspectionOutcome.SUCCESS, _SOURCE).to_json()
    )
    document["chapter_rows_omitted"] = value
    with pytest.raises(ValueError, match="protocol"):
        AudioInspection.from_json(
            json.dumps(document, sort_keys=True, separators=(",", ":"))
        )


def test_child_protocol_refuses_missing_omission_and_actual_v3_producer() -> None:
    document = json.loads(
        AudioInspection(AudioInspectionOutcome.SUCCESS, _SOURCE).to_json()
    )
    del document["chapter_rows_omitted"]
    with pytest.raises(ValueError, match="protocol"):
        AudioInspection.from_json(json.dumps(document))
    old = (
        Path(__file__).parents[1]
        / "support/audio_prior_producer/audio-inspection-v3.json"
    )
    with pytest.raises(ValueError, match="protocol"):
        AudioInspection.from_json(old.read_text())


def test_failed_child_cannot_carry_omission_evidence() -> None:
    with pytest.raises(ValueError, match="failed audio inspection"):
        AudioInspection(
            AudioInspectionOutcome.MALFORMED, None, chapter_rows_omitted=True
        )
