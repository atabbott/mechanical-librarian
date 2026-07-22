"""One private, already-preflighted EPUB archive session.

The session is deliberately an implementation value: it has no JSON form,
public protocol, registry, or lifetime beyond one isolated child attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import zipfile


@dataclass(frozen=True)
class EpubArchiveSession:
    """Archive state shared by package and opening inspection in one child."""

    archive: zipfile.ZipFile
    entries: dict[str, Any]
    package_name: str
    package_root: Any
    container_root: Any
    encryption_names: frozenset[str]
    usage: Any

    def __post_init__(self) -> None:
        if (
            type(self.archive) is not zipfile.ZipFile
            or type(self.entries) is not dict
            or type(self.package_name) is not str
            or not self.package_name
            or type(self.encryption_names) is not frozenset
        ):
            raise ValueError("EPUB archive session is malformed")


__all__ = ["EpubArchiveSession"]
