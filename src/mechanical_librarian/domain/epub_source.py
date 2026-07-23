"""Private logical file reference used only inside the EPUB inspector child."""

from __future__ import annotations

from dataclasses import dataclass

from mechanical_librarian.domain.identifiers import DomainValueError


@dataclass(frozen=True, repr=False)
class FileReference:
    mount: str
    relative_path: str

    def __post_init__(self) -> None:
        if type(self.mount) is not str or not self.mount or "/" in self.mount or self.mount.strip() != self.mount:
            raise DomainValueError("file mount is invalid")
        parts = self.relative_path.split("/") if type(self.relative_path) is str else ()
        if not parts or self.relative_path.startswith("/") or any(part in {"", ".", ".."} for part in parts) or "\x00" in self.relative_path:
            raise DomainValueError("file reference is invalid")


__all__ = ["FileReference"]
