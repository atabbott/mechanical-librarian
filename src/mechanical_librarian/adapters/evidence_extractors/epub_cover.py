"""Parent protocol for declared EPUB cover bytes; no heuristic discovery."""
from __future__ import annotations
import json, os, subprocess, sys, threading
from dataclasses import dataclass
from mechanical_librarian.domain.cover_evidence import CoverSourceKind, CoverSourceState, cover_source_id

@dataclass(frozen=True, slots=True)
class DeclaredEpubCover:
    source_id: str
    state: CoverSourceState
    media_type: str | None = None
    data: bytes | None = None

def read_declared_cover(descriptor: int, expected_digest: str) -> DeclaredEpubCover:
    try:
        if len(expected_digest) != 64 or any(value not in "0123456789abcdef" for value in expected_digest): raise ValueError
        control_read, control_write = os.pipe()
        cover_read, cover_write = os.pipe()
        context = json.dumps({"capability":"cover", "child_seconds":55.0, "expected_source":{"algorithm":"sha256", "digest":expected_digest, "schema":"local-file-v1"}, "limits":{"profile":"epub-opening-limits-v1", **__import__("mechanical_librarian.domain.epub_opening", fromlist=["EpubOpeningLimits"]).EpubOpeningLimits().to_data()}, "logical_identity":expected_digest}, separators=(",", ":")).encode()
        process = subprocess.Popen([sys.executable, "-I", "-B", "-m", "mechanical_librarian.bootstrap.epub_child", str(descriptor), str(control_read), str(os.getpid()), str(cover_write)], pass_fds=(descriptor, control_read, cover_write), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={"LANG":"C.UTF-8", "LC_ALL":"C.UTF-8", "PYTHONDONTWRITEBYTECODE":"1", "PYTHONUTF8":"1"}, cwd="/", start_new_session=True)
        os.close(control_read); os.close(cover_write)
        cover = bytearray()
        overflow = [False]
        def drain() -> None:
            while True:
                chunk = os.read(cover_read, 65_536)
                if not chunk: break
                cover.extend(chunk)
                if len(cover) > 8 * 1024 * 1024:
                    overflow[0] = True
                    break
        reader = threading.Thread(target=drain)
        reader.start()
        stdout, _ = process.communicate(context, timeout=60)
        os.close(control_write); reader.join(timeout=1); os.close(cover_read)
        value = json.loads(stdout)
        if process.returncode or reader.is_alive() or overflow[0] or type(value) is not dict: raise ValueError
        state = CoverSourceState(value["state"])
        if state is CoverSourceState.ABSENT: return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, "absent"), state)
        if state is CoverSourceState.OVERSIZED: return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, "oversized"), state)
        if set(value) != {"state", "media_type", "length", "identity"} or value["media_type"] not in {"image/jpeg", "image/png", "image/webp"}: raise ValueError
        data = bytes(cover)
        if not data or value["length"] != len(data) or len(data) > 8 * 1024 * 1024: raise ValueError
        return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, value["identity"]), state, value["media_type"], data)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return DeclaredEpubCover(cover_source_id(CoverSourceKind.EPUB, "invalid"), CoverSourceState.INVALID_DECLARATION)
