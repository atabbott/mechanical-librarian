"""Explicit, disposable authority for all workbench scenarios."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class IsolatedNetworkProfile:
    profile_id: str
    sanitized: bool
    bounded_authority: bool
    input_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        if (re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.profile_id) is None
                or not self.sanitized or not self.bounded_authority
                or not self.input_sha256
                or len(self.input_sha256) > 32
                or tuple(sorted(set(self.input_sha256))) != self.input_sha256
                or any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in self.input_sha256)):
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
        candidate = self.workspace / relative
        if candidate.is_symlink():
            raise ValueError("workbench paths cannot be symbolic links")
        resolved = candidate.resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError("workbench path escapes its workspace")
        return resolved

    def require_write(self) -> None:
        if self.target_class not in {"fake", "isolated"}:
            raise PermissionError("production-target writes are refused")

    def require_network(self, *authority_files: Path) -> None:
        if self.network is None:
            raise PermissionError("an isolated network profile is required")
        if not authority_files or len(authority_files) > 8:
            raise PermissionError("bounded network authority files are required")
        digests = tuple(sorted(self._authority_digest(path) for path in authority_files))
        if any(digest not in self.network.input_sha256 for digest in digests):
            raise PermissionError("network authority is not bound to the isolated profile")
        for attempt_number in range(1_000):
            evidence = json.dumps(
                {
                    "attempt_number": attempt_number,
                    "bounded_authority": True,
                    "input_sha256": list(digests),
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

    def require_isolated_configuration(self, path: Path) -> tuple[Path, ...]:
        """Reject production-shaped service origins and private host paths."""
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PermissionError("isolated configuration is invalid") from None
        if type(value) is not dict:
            raise PermissionError("isolated configuration is invalid")
        connections = value.get("connections", [])
        if type(connections) is not list:
            raise PermissionError("isolated configuration is invalid")
        for connection in connections:
            if type(connection) is not dict or type(connection.get("endpoint")) is not str:
                raise PermissionError("isolated configuration is invalid")
            endpoint = urlsplit(connection["endpoint"])
            hostname = endpoint.hostname or ""
            if (endpoint.scheme not in {"http", "https"}
                    or hostname not in {"127.0.0.1", "::1", "localhost"}
                    and not hostname.endswith(".invalid")):
                raise PermissionError("production service origins are refused")
        providers = value.get("providers", [])
        if type(providers) is not list or any(
            type(provider) is not dict or provider.get("enabled") is True
            for provider in providers
        ):
            raise PermissionError("production provider origins are refused")
        authority_files: list[Path] = []
        self._require_private_paths_confined(value, authority_files)
        return tuple(authority_files)

    def _require_private_paths_confined(
        self,
        value: object,
        authority_files: list[Path],
    ) -> None:
        if type(value) is dict:
            for key, item in value.items():
                if ((key.endswith("_file") or key == "container_path")
                        and type(item) is str):
                    candidate = Path(item)
                    resolved = (
                        candidate.resolve() if candidate.is_absolute()
                        else (self.workspace / candidate).resolve()
                    )
                    if resolved != self.workspace and self.workspace not in resolved.parents:
                        raise PermissionError("network configuration path escapes its workspace")
                    if key.endswith("_file"):
                        authority_files.append(resolved)
                self._require_private_paths_confined(item, authority_files)
        elif type(value) is list:
            for item in value:
                self._require_private_paths_confined(item, authority_files)

    def _authority_digest(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise PermissionError("network authority file escapes its workspace")
        if not resolved.is_file() or path.is_symlink() or resolved.stat().st_size > 1_048_576:
            raise PermissionError("network authority file is invalid")
        return sha256(resolved.read_bytes()).hexdigest()

    def store_artifact(self, payload: bytes) -> tuple[str, str]:
        self.require_write()
        digest = sha256(payload).hexdigest()
        artifact_id = f"workbench_artifact_{digest}"
        target = self.path(f"artifacts/{artifact_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return artifact_id, digest
