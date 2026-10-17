"""Validated documents the Activity reader projects, held by exact bytes (#1231)."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from typing import TypeVar, cast

from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

_Validated = TypeVar("_Validated")

# Document text whose validated objects one store holds. The 2,300-work
# synthetic library's reports, cases, proposals and outcomes need about 75 Mi.
ACTIVITY_DOCUMENT_CHARACTERS = 128 * 1024 * 1024


class ActivityDocuments:
    """What each exact document validated as, for the Activity reader holding it.

    An entry is keyed by its kind and the SHA-256 of the document's exact
    bytes, so only those bytes find it, and the text itself is not kept. The
    held types are deeply immutable and are handed out as they are. A refusal
    is never held, so the same bytes are parsed again and refuse exactly as
    before. Beyond ``max_characters`` of document text the least recently
    used entries are dropped.
    """

    def __init__(self, max_characters: int = ACTIVITY_DOCUMENT_CHARACTERS) -> None:
        self._max_characters = max_characters
        self._lock = Lock()
        self._entries: OrderedDict[tuple[str, str], tuple[object, int]] = OrderedDict()
        self._characters = 0

    def report(self, document: str) -> MetadataReport:
        return self._parsed("metadata_report", MetadataReport.from_json, document)

    def case(self, document: str) -> ReviewCase:
        """A case whose embedded proposal set is reused from the same store."""
        return self._parsed(
            "review_case",
            lambda value: ReviewCase.from_json(value, self.proposal),
            document,
        )

    def proposal(self, document: str) -> ProposalSet:
        return self._parsed("proposal_set", ProposalSet.from_json, document)

    def outcome(self, document: str) -> ShadowOutcomeSet:
        return self._parsed("shadow_outcome_set", ShadowOutcomeSet.from_json, document)

    def _parsed(
        self, kind: str, parse: Callable[[str], _Validated], document: str
    ) -> _Validated:
        if type(document) is not str:
            return parse(document)
        key = (
            kind,
            hashlib.sha256(document.encode("utf-8", "surrogatepass")).hexdigest(),
        )
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return cast("_Validated", entry[0])
        value = parse(document)
        if len(document) <= self._max_characters:
            with self._lock:
                if key not in self._entries:
                    self._entries[key] = (value, len(document))
                    self._characters += len(document)
                    while self._characters > self._max_characters:
                        self._characters -= self._entries.popitem(last=False)[1][1]
        return value
