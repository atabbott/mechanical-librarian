"""Private bounded local timing and re-redacted support evidence."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Callable

from mechanical_librarian.application.operations import (
    MAX_SUPPORT_BYTES, MAX_TIMINGS, OperationsError, StageTiming, canonical_support,
)

_PRIVATE_KEY = re.compile(
    r"(?:secret|token|password|credential|authorization|cookie|path|value|metadata|excerpt|cover|media|environment|database)",
    re.IGNORECASE,
)


class LocalTimingStore:
    """Optional diagnostic ring stored apart from the application database."""

    def __init__(self, path: Path, *, enabled: bool = False) -> None:
        self._path = path
        self._enabled = enabled

    def append(self, timing: StageTiming) -> None:
        if not self._enabled:
            return
        values = [*self.list(), timing][-MAX_TIMINGS:]
        self._write({"schema_version": "stage-timings-v1",
                     "timings": [value.to_data() for value in values]})

    def list(self) -> tuple[StageTiming, ...]:
        if not self._enabled:
            return ()
        try:
            information = self._path.lstat()
        except FileNotFoundError:
            return ()
        if (not stat.S_ISREG(information.st_mode)
                or stat.S_IMODE(information.st_mode) != 0o600
                or information.st_uid != os.geteuid()
                or information.st_size > MAX_SUPPORT_BYTES):
            raise OperationsError("timing_unavailable")
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if type(value) is not dict or set(value) != {"schema_version", "timings"} \
                    or value["schema_version"] != "stage-timings-v1" \
                    or type(value["timings"]) is not list \
                    or len(value["timings"]) > MAX_TIMINGS:
                raise OperationsError("timing_unavailable")
            return tuple(StageTiming.from_data(item) for item in value["timings"])
        except (OSError, UnicodeError, json.JSONDecodeError, OperationsError) as error:
            raise OperationsError("timing_unavailable") from error

    def _write(self, value: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._path.parent, 0o700)
        temporary = self._path.with_suffix(".tmp")
        payload = canonical_support(value)
        try:
            incomplete = temporary.lstat()
        except FileNotFoundError:
            pass
        else:
            if (not stat.S_ISREG(incomplete.st_mode)
                    or incomplete.st_uid != os.geteuid()
                    or stat.S_IMODE(incomplete.st_mode) != 0o600
                    or incomplete.st_size > MAX_SUPPORT_BYTES):
                raise OperationsError("timing_unavailable")
            temporary.unlink()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OperationsError("timing_unavailable")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self._path)
        root = os.open(self._path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(root)
        finally:
            os.close(root)


class SupportBundle:
    """Build the exact preview/export payload from allow-listed producers."""

    def __init__(self, producers: tuple[tuple[str, Callable[[], object]], ...]) -> None:
        if not producers or len(producers) > 16:
            raise OperationsError("support_configuration")
        self._producers = producers

    def preview(self) -> dict[str, object]:
        sections: dict[str, object] = {}
        for name, producer in self._producers:
            if not name or len(name) > 64 or _PRIVATE_KEY.search(name):
                raise OperationsError("support_configuration")
            sections[name] = _redact(producer(), depth=0)
        payload = {"schema_version": "support-bundle-v1", "sections": sections}
        encoded = canonical_support(payload)
        return {"bytes": len(encoded), "excluded": [
            "auth", "covers", "databases", "environment", "excerpts",
            "media", "metadata_values", "private_paths", "recovery_store", "secrets",
        ], "payload": payload, "schema_version": "support-preview-v1"}

    def export(self) -> bytes:
        # Re-run every producer and redaction at export; the preview is never
        # treated as trusted or reused.
        preview = self.preview()
        return canonical_support(preview["payload"])


def _redact(value: object, *, depth: int) -> object:
    if depth > 12:
        raise OperationsError("support_too_deep")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str:
        if len(value) > 256:
            return "[redacted]"
        if value.startswith(("/", "file:", "http://", "https://")):
            return "[redacted]"
        return value
    if type(value) is list or type(value) is tuple:
        return [_redact(item, depth=depth + 1) for item in value[:256]]
    if type(value) is dict:
        result: dict[str, object] = {}
        for key in sorted(value)[:256]:
            if type(key) is not str or _PRIVATE_KEY.search(key):
                continue
            result[key] = _redact(value[key], depth=depth + 1)
        return result
    return "[redacted]"
