"""Explicit, disposable authority for all workbench scenarios."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class IsolatedNetworkProfile:
    profile_id: str
    sanitized: bool
    bounded_authority: bool

    def __post_init__(self) -> None:
        if (re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.profile_id) is None
                or not self.sanitized or not self.bounded_authority):
            raise ValueError("isolated network profile is required")


@dataclass
class WorkbenchContext:
    workspace: Path
    input_class: str = "synthetic"
    approved_private_corpus: bool = False
    target_class: str = "fake"
    network: IsolatedNetworkProfile | None = None
    _temporary: bool = field(default=False, repr=False)

    @classmethod
    def temporary(cls) -> "WorkbenchContext":
        return cls(
            Path(tempfile.mkdtemp(prefix="mechanical-librarian-workbench-")),
            _temporary=True,
        )

    def __post_init__(self) -> None:
        if self.input_class not in {"synthetic", "test", "private-corpus"}:
            raise ValueError("workbench input class is invalid")
        if self.input_class == "private-corpus" and not self.approved_private_corpus:
            raise PermissionError("private corpus approval is required")
        if self.target_class not in {"fake", "isolated"}:
            raise PermissionError("production targets are refused")
        self.workspace = self.workspace.resolve()
        if self.workspace == Path("/") or self.workspace.is_relative_to(Path("/data")):
            raise PermissionError("production workspaces are refused")
        self.workspace.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "WorkbenchContext":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._temporary and self.workspace.exists():
            shutil.rmtree(self.workspace)
            self._temporary = False

    def path(self, relative: str) -> Path:
        if (not relative or len(relative) > 256 or "\x00" in relative
                or Path(relative).is_absolute()):
            raise ValueError("workbench path is invalid")
        resolved = (self.workspace / relative).resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError("workbench path escapes its workspace")
        return resolved

    def require_write(self) -> None:
        if self.target_class not in {"fake", "isolated"}:
            raise PermissionError("production-target writes are refused")

    def require_network(self) -> None:
        if self.network is None:
            raise PermissionError("an isolated network profile is required")
        for attempt_number in range(1_000):
            evidence = json.dumps(
                {
                    "attempt_number": attempt_number,
                    "bounded_authority": True,
                    "network_profile": self.network.profile_id,
                    "sanitized": True,
                    "schema_version": "workbench-attempt-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            attempt = "attempt-" + sha256(evidence.encode()).hexdigest()
            target = self.path(f"attempts/{attempt}.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(evidence + "\n")
                return
            except FileExistsError:
                continue
        raise RuntimeError("workbench attempt limit reached")

    def store_artifact(self, payload: bytes) -> tuple[str, str]:
        self.require_write()
        digest = sha256(payload).hexdigest()
        artifact_id = f"workbench_artifact_{digest}"
        target = self.path(f"artifacts/{artifact_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return artifact_id, digest
