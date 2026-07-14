"""Licence-safe, descriptor-only EPUB generator for the F09a preview."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from typing import BinaryIO, Iterator

from mechanical_librarian.application.title_proposals import (
    GENERATOR_VERSION,
    SyntheticScenario,
)


_CONTAINER = b'''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''


@dataclass(frozen=True)
class GeneratedSyntheticEpub:
    stream: BinaryIO
    descriptor: int
    length: int
    digest: str
    opaque_identity: str


@contextmanager
def generated_synthetic_epub(scenario: SyntheticScenario) -> Iterator[GeneratedSyntheticEpub]:
    """Yield a temporary descriptor and close it on every exit path."""

    if type(scenario) is not SyntheticScenario:
        raise ValueError("synthetic EPUB generation requires a closed scenario")
    stream = tempfile.TemporaryFile(mode="w+b")
    try:
        package = _package_document(scenario.local_titles)
        with zipfile.ZipFile(stream, "w", allowZip64=False) as archive:
            mimetype = zipfile.ZipInfo("mimetype", (2026, 1, 1, 0, 0, 0))
            mimetype.compress_type = zipfile.ZIP_STORED
            archive.writestr(mimetype, b"application/epub+zip")
            for name, value in (
                ("META-INF/container.xml", _CONTAINER),
                ("EPUB/package.opf", package),
                ("EPUB/chapter.xhtml", b"<html xmlns='http://www.w3.org/1999/xhtml'><body>Synthetic preview.</body></html>"),
            ):
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, value)
        length = stream.tell()
        stream.seek(0)
        digest = hashlib.sha256(stream.read()).hexdigest()
        stream.seek(0)
        opaque_identity = hashlib.sha256(
            f"{GENERATOR_VERSION}\x00{scenario.name}\x00{scenario.lineage_revision}".encode("utf-8")
        ).hexdigest()
        yield GeneratedSyntheticEpub(
            stream, stream.fileno(), length, digest, opaque_identity
        )
    finally:
        stream.close()


def _package_document(titles: tuple[str, ...]) -> bytes:
    title_elements = "\n".join(
        f"    <dc:title>{escape(title)}</dc:title>" for title in titles
    )
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="pub-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">synthetic-preview-001</dc:identifier>
{title_elements}
    <dc:creator>Mechanical Librarian Preview</dc:creator>
    <dc:language>en-GB</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>'''
    return document.encode("utf-8")


__all__ = ["GeneratedSyntheticEpub", "generated_synthetic_epub"]
