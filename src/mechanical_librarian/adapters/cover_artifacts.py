"""Isolated, deterministic decoding for the small admitted cover formats."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from mechanical_librarian.domain.cover_evidence import CoverArtifact
from mechanical_librarian.domain.cover_evidence import CoverSourceState


_MAX_ORIGINAL_BYTES = 8 * 1024 * 1024
_MAX_PREVIEW_BYTES = 1024 * 1024
_WALL_SECONDS = 60


class CoverDecodeError(RuntimeError):
    pass


class CoverDecodeTimeout(CoverDecodeError):
    pass


class CoverDecodeRejected(CoverDecodeError):
    """The hostile image is unsupported or outside the closed bounds."""

    def __init__(self, state: CoverSourceState = CoverSourceState.DECODE_FAILED) -> None:
        self.state = state
        super().__init__("cover decode rejected")


@dataclass(frozen=True, slots=True)
class DecodedCover:
    artifact: CoverArtifact
    preview: bytes


def decode_cover(original: bytes, *, deadline: float | None = None) -> DecodedCover:
    """Decode one bounded image in a fresh child with no inherited environment."""
    if type(original) is not bytes or not original or len(original) > _MAX_ORIGINAL_BYTES:
        raise CoverDecodeRejected(CoverSourceState.OVERSIZED)
    timeout = _WALL_SECONDS if deadline is None else min(
        _WALL_SECONDS,
        max(0.0, deadline - time.monotonic()),
    )
    if timeout <= 0:
        raise CoverDecodeTimeout("cover decode timed out")
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "mechanical_librarian.bootstrap.cover_child"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
            start_new_session=True,
        )
        stdout, _ = process.communicate(original, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
        raise CoverDecodeTimeout("cover decode timed out") from error
    except OSError as error:
        raise CoverDecodeError("cover decoder is unavailable") from error
    if process.returncode != 0 or len(stdout) > _MAX_PREVIEW_BYTES * 2:
        raise CoverDecodeRejected(CoverSourceState.DECODE_FAILED)
    try:
        value = json.loads(stdout)
        if type(value) is not dict or type(value.get("state")) is not str:
            raise ValueError
        state = CoverSourceState(value["state"])
        if state is not CoverSourceState.RETRIEVED:
            raise CoverDecodeRejected(state)
        if set(value) != {"state", "format", "height", "preview", "width"}:
            raise ValueError
        preview = base64.b64decode(value["preview"], validate=True)
        if type(value["format"]) is not str or type(value["width"]) is not int or type(value["height"]) is not int or len(preview) > _MAX_PREVIEW_BYTES:
            raise ValueError
    except CoverDecodeRejected:
        raise
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise CoverDecodeRejected(CoverSourceState.DECODE_FAILED) from error
    return DecodedCover(CoverArtifact.create(original, preview, value["format"], value["width"], value["height"]), preview)


__all__ = [
    "CoverDecodeError",
    "CoverDecodeRejected",
    "CoverDecodeTimeout",
    "DecodedCover",
    "decode_cover",
]
