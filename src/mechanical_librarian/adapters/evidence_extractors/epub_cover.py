"""Parent protocol for declared EPUB cover bytes; no heuristic discovery."""
from __future__ import annotations
import base64, json, os, subprocess, sys
from dataclasses import dataclass
from mechanical_librarian.domain.cover_evidence import CoverSourceKind, CoverSourceState, cover_source_id

@dataclass(frozen=True, slots=True)
class DeclaredEpubCover:
    source_id: str
    state: CoverSourceState
    media_type: str | None = None
    data: bytes | None = None

def read_declared_cover(descriptor: int) -> DeclaredEpubCover:
    try:
        process = subprocess.run([sys.executable, "-m", "mechanical_librarian.bootstrap.epub_cover_child", str(descriptor)], pass_fds=(descriptor,), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False, env={"LANG":"C.UTF-8", "LC_ALL":"C.UTF-8", "PYTHONDONTWRITEBYTECODE":"1"})
        value = json.loads(process.stdout)
        if process.returncode or type(value) is not dict: raise ValueError
        state = CoverSourceState(value["state"])
        if state is CoverSourceState.ABSENT: return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, "absent"), state)
        if state is CoverSourceState.OVERSIZED: return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, "oversized"), state)
        if set(value) != {"state", "media_type", "data", "identity"} or value["media_type"] not in {"image/jpeg", "image/png", "image/webp"}: raise ValueError
        data = base64.b64decode(value["data"], validate=True)
        if not data or len(data) > 8 * 1024 * 1024: raise ValueError
        return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, value["identity"]), state, value["media_type"], data)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, "invalid"), CoverSourceState.INVALID_DECLARATION)
