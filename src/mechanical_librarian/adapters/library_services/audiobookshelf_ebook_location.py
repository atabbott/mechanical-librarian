"""Canonical service-relative Audiobookshelf ebook location parsing."""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass

from mechanical_librarian.domain.metadata_report import canonical_json


@dataclass(frozen=True)
class EbookLocation:
    """One validated ebook location and its stable service identity."""

    relative_path: str
    format: str
    identity: str


def normalize_ebook_location(
    source: object,
    *,
    service_path: str | None,
    item_relative: object = None,
) -> EbookLocation:
    """Validate one supported file record without guessing conflicting paths."""
    if type(source) is not dict:
        raise ValueError("ebook file mapping is invalid")
    metadata = source.get("metadata")
    if type(metadata) is not dict:
        raise ValueError("ebook file mapping is invalid")
    format_value = source.get("ebookFormat")
    if type(format_value) is not str:
        extension = metadata.get("ext")
        format_value = extension[1:] if type(extension) is str and extension.startswith(".") else ""
    format_value = format_value.lower()
    if not 1 <= len(format_value) <= 16 or not format_value.isascii() or not format_value.isalnum():
        raise ValueError("ebook file mapping is invalid")
    if item_relative is not None and not is_safe_relative_path(item_relative):
        raise ValueError("ebook file mapping is invalid")

    relative = metadata.get("relPath")
    path = metadata.get("path")
    has_relative = type(relative) is str and bool(relative)
    if relative is not None and not has_relative:
        raise ValueError("ebook file mapping is invalid")
    from_path = None
    if path is not None:
        if type(path) is not str or service_path is None:
            raise ValueError("ebook file mapping is invalid")
        from_path = relative_service_path(path, service_path)
    if not has_relative:
        if from_path is None:
            raise ValueError("ebook file mapping is invalid")
        relative = from_path
    elif not is_safe_relative_path(relative):
        raise ValueError("ebook file mapping is invalid")
    elif from_path is not None:
        item_resolved = None if item_relative is None else posixpath.join(item_relative, relative)
        if from_path != relative and from_path != item_resolved:
            raise ValueError("ebook file mapping is invalid")
        relative = from_path
    if not is_safe_relative_path(relative):
        raise ValueError("ebook file mapping is invalid")
    size = metadata.get("size")
    inode = source.get("ino", metadata.get("ino"))
    if type(size) is not int or size < 0 or type(inode) is not str or not 1 <= len(inode) <= 256:
        raise ValueError("ebook file identity is invalid")
    identity = hashlib.sha256(
        canonical_json(
            {"format": format_value, "inode": inode, "relative": relative, "size": size}
        ).encode()
    ).hexdigest()
    return EbookLocation(relative, format_value, identity)


def is_safe_relative_path(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 1024
        and posixpath.normpath(value) == value
        and not value.startswith("/")
        and not any(part in {"", ".", ".."} for part in value.split("/"))
    )


def relative_service_path(path: str, service_path: str) -> str:
    normalized_service_path = service_path.rstrip("/") or "/"
    if normalized_service_path == "/":
        if not path.startswith("/") or path == "/":
            raise ValueError("ebook file mapping is invalid")
        return path[1:]
    prefix = normalized_service_path + "/"
    if not path.startswith(prefix):
        raise ValueError("ebook file mapping is invalid")
    return path[len(prefix) :]


__all__ = ["EbookLocation", "is_safe_relative_path", "normalize_ebook_location"]
