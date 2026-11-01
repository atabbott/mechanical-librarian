"""Parent protocol for declared EPUB cover bytes; no heuristic discovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass

from mechanical_librarian.adapters.evidence_extractors.process import (
    _minimal_environment,
    _stop,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverSourceKind,
    CoverSourceState,
    EpubCoverRecovery,
    cover_source_id,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits

_MAX_COVER_BYTES = 8 * 1024 * 1024
_WALL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class DeclaredEpubCover:
    source_id: str
    state: CoverSourceState | None
    media_type: str | None = None
    data: bytes | None = None
    # Shadow evidence from the cover fallbacks; only ever beside ``absent``.
    recovery: EpubCoverRecovery | None = None


def _close_descriptor(descriptor: int) -> int:
    if descriptor >= 0:
        try:
            os.close(descriptor)
        except OSError:
            pass
    return -1


def read_declared_cover(descriptor: int, expected_digest: str) -> DeclaredEpubCover:
    control_read = -1
    control_write = -1
    cover_read = -1
    cover_write = -1
    process: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    reader_started = False
    cover = bytearray()
    overflow = [False]
    try:
        if len(expected_digest) != 64 or any(
            value not in "0123456789abcdef" for value in expected_digest
        ):
            raise ValueError
        control_read, control_write = os.pipe()
        cover_read, cover_write = os.pipe()
        context = json.dumps(
            {
                "capability": "cover",
                "child_seconds": 55.0,
                "expected_source": {
                    "algorithm": "sha256",
                    "digest": expected_digest,
                    "schema": "local-file-v1",
                },
                "limits": {
                    "profile": "epub-opening-limits-v1",
                    **EpubOpeningLimits().to_data(),
                },
                "logical_identity": expected_digest,
            },
            separators=(",", ":"),
        ).encode()
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "mechanical_librarian.bootstrap.epub_child",
                str(descriptor),
                str(control_read),
                str(os.getpid()),
                str(cover_write),
            ],
            pass_fds=(descriptor, control_read, cover_write),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=_minimal_environment(),
            cwd="/",
            start_new_session=True,
            shell=False,
        )
        control_read = _close_descriptor(control_read)
        cover_write = _close_descriptor(cover_write)

        def drain() -> None:
            while True:
                try:
                    chunk = os.read(cover_read, 65_536)
                except OSError:
                    return
                if not chunk:
                    return
                if overflow[0]:
                    continue
                if len(cover) + len(chunk) > _MAX_COVER_BYTES:
                    overflow[0] = True
                    cover.clear()
                    continue
                cover.extend(chunk)

        reader = threading.Thread(target=drain)
        reader.start()
        reader_started = True
        stdout, _ = process.communicate(context, timeout=_WALL_SECONDS)
        reader.join(timeout=1.0)
        if process.returncode or reader.is_alive() or overflow[0]:
            raise ValueError
        value = json.loads(stdout)
        if type(value) is not dict:
            raise ValueError
        state_value = value["state"]
        state = None if state_value == "declared" else CoverSourceState(state_value)
        if state is CoverSourceState.ABSENT:
            if set(value) not in ({"state"}, {"state", "recovery"}):
                raise ValueError
            return DeclaredEpubCover(
                cover_source_id(CoverSourceKind.EPUB, "absent"),
                state,
                recovery=(
                    EpubCoverRecovery.from_data(value["recovery"])
                    if "recovery" in value
                    else None
                ),
            )
        if "recovery" in value:
            raise ValueError
        if state is CoverSourceState.OVERSIZED:
            return DeclaredEpubCover(
                cover_source_id(CoverSourceKind.EPUB, "oversized"),
                state,
            )
        if state in {
            CoverSourceState.INVALID_DECLARATION,
            CoverSourceState.UNSUPPORTED_FORMAT,
        }:
            return DeclaredEpubCover(
                cover_source_id(CoverSourceKind.EPUB, state.value),
                state,
            )
        if state is not None or (
            set(value) != {"state", "media_type", "length", "identity"}
            or value["media_type"] not in {"image/jpeg", "image/png", "image/webp"}
        ):
            raise ValueError
        data = bytes(cover)
        if not data or value["length"] != len(data) or len(data) > _MAX_COVER_BYTES:
            raise ValueError
        return DeclaredEpubCover(
            cover_source_id(CoverSourceKind.EPUB, value["identity"]),
            state,
            value["media_type"],
            data,
        )
    except subprocess.TimeoutExpired:
        return DeclaredEpubCover(
            cover_source_id(CoverSourceKind.EPUB, "timeout"),
            CoverSourceState.TIMEOUT,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ):
        return DeclaredEpubCover(
            cover_source_id(CoverSourceKind.EPUB, "invalid"),
            CoverSourceState.INVALID_DECLARATION,
        )
    finally:
        control_write = _close_descriptor(control_write)
        if process is not None:
            _stop(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        control_read = _close_descriptor(control_read)
        cover_write = _close_descriptor(cover_write)
        if reader is not None and reader_started:
            reader.join(timeout=1.0)
            if reader.is_alive():
                cover_read = _close_descriptor(cover_read)
                reader.join(timeout=1.0)
        cover_read = _close_descriptor(cover_read)
