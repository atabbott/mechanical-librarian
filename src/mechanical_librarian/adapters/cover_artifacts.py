"""Isolated, deterministic decoding for the small admitted cover formats."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from mechanical_librarian.domain.cover_evidence import CoverArtifact


_MAX_ORIGINAL_BYTES = 8 * 1024 * 1024
_MAX_PREVIEW_BYTES = 1024 * 1024
_WALL_SECONDS = 60


class CoverDecodeError(RuntimeError):
    pass


class CoverDecodeRejected(CoverDecodeError):
    """The hostile image is unsupported or outside the closed bounds."""


@dataclass(frozen=True, slots=True)
class DecodedCover:
    artifact: CoverArtifact
    preview: bytes


def decode_cover(original: bytes) -> DecodedCover:
    """Decode one bounded image in a fresh child with no inherited environment."""
    if type(original) is not bytes or not original or len(original) > _MAX_ORIGINAL_BYTES:
        raise CoverDecodeRejected("cover is oversized")
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
        stdout, _ = process.communicate(original, timeout=_WALL_SECONDS)
    except subprocess.TimeoutExpired as error:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
        raise CoverDecodeRejected("cover decode timed out") from error
    except OSError as error:
        raise CoverDecodeError("cover decoder is unavailable") from error
    if process.returncode != 0 or len(stdout) > _MAX_PREVIEW_BYTES * 2:
        raise CoverDecodeRejected("cover decode failed")
    try:
        value = json.loads(stdout)
        if type(value) is not dict or set(value) != {"format", "height", "preview", "width"}:
            raise ValueError
        preview = base64.b64decode(value["preview"], validate=True)
        if type(value["format"]) is not str or type(value["width"]) is not int or type(value["height"]) is not int or len(preview) > _MAX_PREVIEW_BYTES:
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise CoverDecodeRejected("cover decoder returned invalid output") from error
    return DecodedCover(CoverArtifact.create(original, preview, value["format"], value["width"], value["height"]), preview)


__all__ = ["CoverDecodeError", "CoverDecodeRejected", "DecodedCover", "decode_cover"]
