"""Owner-private bounded cover recovery storage for non-terminal effects."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from mechanical_librarian.domain.field_effect import FieldEffectPlan


class PrivateCoverRecoveryStore:
    def __init__(self, root: Path, reader, maximum_bytes: int = 8 * 1024 * 1024,
                 maximum_entries: int = 128) -> None:
        if not 1 <= maximum_bytes <= 32 * 1024 * 1024 or not 1 <= maximum_entries <= 512:
            raise ValueError("cover recovery configuration")
        self._root = root
        self._reader = reader
        self._maximum_bytes = maximum_bytes
        self._maximum_entries = maximum_entries
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)

    def capture(self, plan: FieldEffectPlan) -> None:
        path = self._path(plan.plan_id)
        temporary = path.with_suffix(".recovery.tmp")
        self._remove_incomplete(temporary)
        try:
            information = path.lstat()
        except FileNotFoundError:
            information = None
        if information is not None:
            self._require_private_file(information)
            return
        existing = tuple(self._root.glob("*.recovery"))
        if len(existing) >= self._maximum_entries:
            raise ValueError("cover recovery full")
        value = self._reader(plan)
        if value is None:
            value = b"ABSENT\n"
        if type(value) is not bytes or len(value) > self._maximum_bytes:
            raise ValueError("cover recovery unavailable")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            view = memoryview(value)
            while view:
                count = os.write(descriptor, view)
                if count <= 0:
                    raise OSError("cover recovery unavailable")
                view = view[count:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        self._sync_root()

    def remove(self, plan_id: str) -> None:
        removed = False
        for path in (self._path(plan_id), self._path(plan_id).with_suffix(".recovery.tmp")):
            try:
                information = path.lstat()
            except FileNotFoundError:
                continue
            self._require_private_file(information)
            path.unlink()
            removed = True
        if removed:
            self._sync_root()

    def exists(self, plan_id: str) -> bool:
        try:
            information = self._path(plan_id).lstat()
        except FileNotFoundError:
            return False
        self._require_private_file(information)
        return True

    def _path(self, plan_id: str) -> Path:
        digest = plan_id.removeprefix("effect_plan_")
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("invalid plan identity")
        return self._root / f"{digest}.recovery"

    def _remove_incomplete(self, path: Path) -> None:
        try:
            information = path.lstat()
        except FileNotFoundError:
            return
        self._require_private_file(information)
        path.unlink()
        self._sync_root()

    def _require_private_file(self, information: os.stat_result) -> None:
        if (not stat.S_ISREG(information.st_mode)
                or stat.S_IMODE(information.st_mode) != 0o600
                or information.st_uid != os.geteuid()
                or information.st_size > self._maximum_bytes):
            raise ValueError("cover recovery unavailable")

    def _sync_root(self) -> None:
        descriptor = os.open(self._root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
