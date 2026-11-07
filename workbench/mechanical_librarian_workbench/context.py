"""Explicit, disposable authority for all workbench scenarios."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

MAX_AUTHORITY_BYTES = 1_048_576
# One configuration, one batch manifest, 32 connection secrets, and the three
# review files admitted by the isolated subset of the exact-current schema.
MAX_NETWORK_AUTHORITY_FILES = 37


def _require_private_directory(root: Path, error: str) -> None:
    """Create or validate one owner-only workbench directory."""
    try:
        information = root.lstat()
    except FileNotFoundError:
        root.mkdir(parents=True, mode=0o700)
        os.chmod(root, 0o700)
        information = root.lstat()
    if (
        not stat.S_ISDIR(information.st_mode)
        or stat.S_IMODE(information.st_mode) != 0o700
        or information.st_uid != os.geteuid()
    ):
        raise ValueError(error)
    descriptor = os.open(
        root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    os.close(descriptor)


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
                or len(self.input_sha256) > MAX_NETWORK_AUTHORITY_FILES
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
    _workspace_root: Path = field(init=False, repr=False)
    _cleanup_workspace: Path | None = field(default=None, init=False, repr=False)
    _cleanup_identity: tuple[int, int] | None = field(default=None, init=False, repr=False)

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
        supplied_workspace = self.workspace
        try:
            if supplied_workspace.is_symlink():
                raise PermissionError("workbench workspace is invalid")
        except OSError:
            raise PermissionError("workbench workspace is invalid") from None
        self.workspace = supplied_workspace.resolve()
        if self.workspace == Path("/") or self.workspace.is_relative_to(Path("/data")):
            raise PermissionError("production workspaces are refused")
        try:
            _require_private_directory(
                self.workspace, "workbench private directory is invalid"
            )
            information = self.workspace.lstat()
        except (OSError, ValueError):
            raise PermissionError("workbench workspace is invalid") from None
        self._workspace_root = self.workspace
        if self._temporary:
            self._cleanup_workspace = self.workspace
            self._cleanup_identity = (information.st_dev, information.st_ino)

    def __enter__(self) -> "WorkbenchContext":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        cleanup = self._cleanup_workspace
        identity = self._cleanup_identity
        if not self._temporary or cleanup is None or identity is None:
            return
        try:
            information = cleanup.lstat()
        except FileNotFoundError:
            self._temporary = False
            self._cleanup_workspace = None
            self._cleanup_identity = None
            return
        if (
            not stat.S_ISDIR(information.st_mode)
            or (information.st_dev, information.st_ino) != identity
        ):
            raise PermissionError("temporary workbench cleanup authority changed")
        shutil.rmtree(cleanup)
        self._temporary = False
        self._cleanup_workspace = None
        self._cleanup_identity = None

    def path(self, relative: str) -> Path:
        if (not relative or len(relative) > 256 or "\x00" in relative
                or Path(relative).is_absolute()):
            raise ValueError("workbench path is invalid")
        candidate = self._workspace_root / relative
        if candidate.is_symlink():
            raise ValueError("workbench paths cannot be symbolic links")
        resolved = candidate.resolve()
        if resolved != self._workspace_root and self._workspace_root not in resolved.parents:
            raise ValueError("workbench path escapes its workspace")
        return resolved

    def require_write(self) -> None:
        if self.target_class not in {"fake", "isolated"}:
            raise PermissionError("production-target writes are refused")

    def require_network(
        self,
        *authority_files: Path,
        private_first: bool = False,
        private_tail: int = 0,
        first_payload: bytes | None = None,
    ) -> tuple[bytes, ...]:
        if self.network is None:
            raise PermissionError("an isolated network profile is required")
        if (
            not authority_files
            or len(authority_files) > MAX_NETWORK_AUTHORITY_FILES
            or type(private_tail) is not int
            or not 0 <= private_tail <= len(authority_files)
        ):
            raise PermissionError("bounded network authority files are required")
        authorities: list[tuple[str, bytes]] = []
        for index, path in enumerate(authority_files):
            if index == 0 and first_payload is not None:
                self._require_workspace_path(path)
                if (
                    type(first_payload) is not bytes
                    or not 1 <= len(first_payload) <= MAX_AUTHORITY_BYTES
                    or private_first
                ):
                    raise PermissionError("network authority file is invalid")
                authorities.append((sha256(first_payload).hexdigest(), first_payload))
            else:
                authorities.append(
                    self._authority_payload(
                        path,
                        private=(
                            private_first and index == 0
                            or index >= len(authority_files) - private_tail
                        ),
                    )
                )
        authority_tuple = tuple(authorities)
        digests = tuple(sorted(digest for digest, _ in authority_tuple))
        if any(digest not in self.network.input_sha256 for digest, _ in authority_tuple):
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
            target = self._private_directory("attempts") / f"{attempt}.json"
            try:
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    attempt_payload = (evidence + "\n").encode("utf-8")
                    offset = 0
                    while offset < len(attempt_payload):
                        written = os.write(descriptor, attempt_payload[offset:])
                        if written <= 0:
                            raise PermissionError("workbench attempt evidence is invalid")
                        offset += written
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                directory = os.open(
                    target.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                return tuple(payload for _, payload in authority_tuple)
            except FileExistsError:
                continue
        raise RuntimeError("workbench attempt limit reached")

    def require_isolated_configuration(
        self, path: Path
    ) -> tuple[bytes, tuple[Path, ...]]:
        """Reject production-shaped service origins and private host paths."""
        try:
            _, payload = self._authority_payload(path)
            value = json.loads(payload.decode("utf-8", errors="strict"))
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
        return payload, tuple(authority_files)

    def authority_snapshot(self, payload: bytes) -> Path:
        """Persist exact admitted bytes in one owner-only execution snapshot."""
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_AUTHORITY_BYTES:
            raise PermissionError("network authority file is invalid")
        digest = sha256(payload).hexdigest()
        authority_directory = self._private_directory("authority")
        target = authority_directory / f"authority-{digest}"
        descriptor = -1
        temporary_path: Path | None = None
        try:
            descriptor, raw_temporary_path = tempfile.mkstemp(
                prefix=f".authority-{digest}-",
                dir=authority_directory,
            )
            temporary_path = Path(raw_temporary_path)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise PermissionError("network authority snapshot is invalid")
                offset += written
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary_path, target, follow_symlinks=False)
            except FileExistsError:
                existing_digest, existing = self._authority_payload(
                    target, private=True
                )
                if existing_digest != digest or existing != payload:
                    raise PermissionError(
                        "network authority snapshot is invalid"
                    ) from None
            directory = os.open(
                authority_directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except PermissionError:
            raise
        except OSError:
            raise PermissionError("network authority snapshot is invalid") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    raise PermissionError(
                        "network authority snapshot is invalid"
                    ) from None
        return target

    def execution_configuration_snapshot(
        self,
        payload: bytes,
        private_files: tuple[Path, ...],
        private_snapshots: tuple[Path, ...],
    ) -> Path:
        """Rebind private file references while preserving admitted config values."""
        if len(private_files) != len(private_snapshots):
            raise PermissionError("isolated configuration is invalid")
        bindings = {
            original.resolve(): snapshot
            for original, snapshot in zip(
                private_files, private_snapshots, strict=True
            )
        }
        try:
            value = json.loads(
                payload.decode("utf-8", errors="strict"),
                object_pairs_hook=self._unique_object,
                parse_constant=lambda _value: self._invalid_configuration(),
            )
            rebound = self._rebind_private_files(value, bindings)
            serialized = json.dumps(
                rebound,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, UnicodeError, json.JSONDecodeError):
            raise PermissionError("isolated configuration is invalid") from None
        return self.authority_snapshot(serialized)

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
                    if key.endswith("_file") and not candidate.is_absolute():
                        raise PermissionError(
                            "network configuration private file must be absolute"
                        )
                    resolved = (
                        candidate.resolve() if candidate.is_absolute()
                        else (self._workspace_root / candidate).resolve()
                    )
                    if (
                        resolved != self._workspace_root
                        and self._workspace_root not in resolved.parents
                    ):
                        raise PermissionError("network configuration path escapes its workspace")
                    if key.endswith("_file") and resolved not in authority_files:
                        authority_files.append(resolved)
                self._require_private_paths_confined(item, authority_files)
        elif type(value) is list:
            for item in value:
                self._require_private_paths_confined(item, authority_files)

    def _rebind_private_files(
        self,
        value: object,
        bindings: dict[Path, Path],
    ) -> object:
        if type(value) is dict:
            rebound: dict[str, object] = {}
            for key, item in value.items():
                if key.endswith("_file") and type(item) is str:
                    original = Path(item)
                    if not original.is_absolute() or original.resolve() not in bindings:
                        raise PermissionError("isolated configuration is invalid")
                    rebound[key] = str(bindings[original.resolve()])
                else:
                    rebound[key] = self._rebind_private_files(item, bindings)
            return rebound
        if type(value) is list:
            return [self._rebind_private_files(item, bindings) for item in value]
        return value

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise PermissionError("isolated configuration is invalid")
            value[key] = item
        return value

    @staticmethod
    def _invalid_configuration() -> object:
        raise PermissionError("isolated configuration is invalid")

    def _authority_payload(self, path: Path, *, private: bool = False) -> tuple[str, bytes]:
        self._require_workspace_path(path)
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            information = os.fstat(descriptor)
            if (
                not stat.S_ISREG(information.st_mode)
                or not 1 <= information.st_size <= MAX_AUTHORITY_BYTES
                or private
                and (
                    information.st_uid != os.geteuid()
                    or information.st_mode & 0o077
                )
            ):
                raise PermissionError("network authority file is invalid")
            chunks: list[bytes] = []
            remaining = information.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65_536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != information.st_size:
                raise PermissionError("network authority file is invalid")
        except OSError:
            raise PermissionError("network authority file is invalid")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return sha256(payload).hexdigest(), payload

    def _require_workspace_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if (
            resolved != self._workspace_root
            and self._workspace_root not in resolved.parents
        ):
            raise PermissionError("network authority file escapes its workspace")
        return resolved

    def _private_directory(self, relative: str) -> Path:
        target = self.path(relative)
        try:
            _require_private_directory(
                target, "workbench private directory is invalid"
            )
        except (OSError, ValueError):
            raise PermissionError("workbench private directory is invalid") from None
        return target

    def store_artifact(self, payload: bytes) -> tuple[str, str]:
        self.require_write()
        digest = sha256(payload).hexdigest()
        artifact_id = f"workbench_artifact_{digest}"
        target = self._private_directory("artifacts") / artifact_id
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            self._require_existing_artifact(target, payload, digest)
            return artifact_id, digest
        except OSError:
            raise PermissionError("workbench artifact is invalid") from None
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise PermissionError("workbench artifact is invalid")
                offset += written
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return artifact_id, digest

    def _require_existing_artifact(
        self, target: Path, payload: bytes, expected_digest: str
    ) -> None:
        self._require_workspace_path(target)
        descriptor = -1
        try:
            descriptor = os.open(
                target,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            information = os.fstat(descriptor)
            if (
                not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()
                or information.st_mode & 0o077
                or information.st_size != len(payload)
            ):
                raise PermissionError("workbench artifact is invalid")
            digest = sha256()
            offset = 0
            while offset < len(payload):
                chunk = os.read(descriptor, min(65_536, len(payload) - offset))
                if not chunk or chunk != payload[offset:offset + len(chunk)]:
                    raise PermissionError("workbench artifact is invalid")
                digest.update(chunk)
                offset += len(chunk)
            if os.read(descriptor, 1) or digest.hexdigest() != expected_digest:
                raise PermissionError("workbench artifact is invalid")
        except PermissionError:
            raise
        except OSError:
            raise PermissionError("workbench artifact is invalid") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
