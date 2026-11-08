"""A stand-in audio child: the real decoder and grammar, no process or ffprobe.

The parent hands it the descriptors it would hand the isolated child; it reads
each with the child's own strict decoder and interprets them with the real
grammar over a fixed synthetic probe document (#1481).
"""

from __future__ import annotations

import json

from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    AudioPathContext,
    AudioSidecar,
    interpret,
)
from mechanical_librarian.bootstrap import audio_child
from mechanical_librarian.domain.audio_inspection import (
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionLimits,
)
from mechanical_librarian.domain.identifiers import Fingerprint

_PROBE = json.dumps(
    {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "4.000"},
        "streams": [{"codec_type": "audio", "codec_name": "aac"}],
        "chapters": [],
    }
)


class GrammarChild:
    """The child's decoder and grammar, without the process or ffprobe."""

    def __init__(self) -> None:
        self.handed: list[tuple[str, ...]] = []

    def __call__(
        self,
        _descriptor: int,
        expected: Fingerprint,
        limits: AudioInspectionLimits,
        *,
        sidecars: tuple[tuple[str, str, int], ...] = (),
        path_file: str = "",
        path_folder: str | None = None,
        **_keywords: object,
    ) -> AudioInspection:
        self.handed.append(tuple(name for _, name, _ in sidecars))
        documents = []
        for surface, name, descriptor in sidecars:
            text = audio_child._sidecar_text(descriptor, limits.sidecar_bytes)
            if text is not None:
                documents.append(
                    AudioSidecar(AudioEvidenceSurface(surface), name, text)
                )
        result = interpret(
            _PROBE,
            tuple(documents),
            AudioPathContext(path_file, path_folder),
            expected,
            limits,
        )
        return AudioInspection(
            result.outcome,
            expected,
            result.facts,
            result.chapters,
            result.embedded_cover,
            result.chapters_truncated,
            result.title,
            result.container_facts,
            result.chapter_rows_omitted,
            result.album,
            result.creators,
            result.sidecars,
        )
