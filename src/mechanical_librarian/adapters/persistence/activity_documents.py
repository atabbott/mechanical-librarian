"""Validated metadata reports the Activity reader projects, held by exact bytes (#1231)."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from threading import Lock

from mechanical_librarian.domain.metadata_report import MetadataReport

# Report text one store holds: twice the 2,300-work synthetic library's reports.
ACTIVITY_DOCUMENT_CHARACTERS = 48 * 1024 * 1024


class ActivityDocuments:
    """What each exact report document validated as, for the readers holding it.

    An entry is keyed by the SHA-256 of the document's exact bytes, so only
    those bytes find it, and the text itself is not kept. A report is deeply
    immutable and is handed out as it is. A refusal is never held, so the same
    bytes are parsed again and refuse exactly as before. Beyond
    ``max_characters`` of document text the least recently used entries are
    dropped.
    """

    def __init__(self, max_characters: int = ACTIVITY_DOCUMENT_CHARACTERS) -> None:
        self._max_characters = max_characters
        self._lock = Lock()
        self._entries: OrderedDict[str, tuple[MetadataReport, int]] = OrderedDict()
        self._characters = 0

    def report(self, document: str) -> MetadataReport:
        if type(document) is not str:
            return MetadataReport.from_json(document)
        key = hashlib.sha256(document.encode("utf-8", "surrogatepass")).hexdigest()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry[0]
        value = MetadataReport.from_json(document)
        if len(document) <= self._max_characters:
            with self._lock:
                if key not in self._entries:
                    self._entries[key] = (value, len(document))
                    self._characters += len(document)
                    while self._characters > self._max_characters:
                        self._characters -= self._entries.popitem(last=False)[1][1]
        return value
