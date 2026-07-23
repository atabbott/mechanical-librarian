"""Parent protocol for declared EPUB cover bytes; no heuristic discovery."""
from __future__ import annotations
import base64, hashlib, json, os, subprocess, sys
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
        size = os.fstat(descriptor).st_size
        snapshot = os.pread(descriptor, size, 0)
        if len(snapshot) != size: raise ValueError
        digest = hashlib.sha256(snapshot).hexdigest()
        control_read, control_write = os.pipe()
        context = json.dumps({"capability":"cover", "child_seconds":55.0, "expected_source":{"algorithm":"sha256", "digest":digest, "schema":"local-file-v1"}, "limits":{"profile":"epub-opening-limits-v1", **__import__("mechanical_librarian.domain.epub_opening", fromlist=["EpubOpeningLimits"]).EpubOpeningLimits().to_data()}, "logical_identity":digest}, separators=(",", ":")).encode()
        process = subprocess.run([sys.executable, "-m", "mechanical_librarian.bootstrap.epub_child", str(descriptor), str(control_read), str(os.getpid())], pass_fds=(descriptor, control_read), input=context, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False, env={"LANG":"C.UTF-8", "LC_ALL":"C.UTF-8", "PYTHONDONTWRITEBYTECODE":"1"})
        os.close(control_read); os.close(control_write)
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
