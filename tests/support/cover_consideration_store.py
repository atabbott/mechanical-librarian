"""An in-memory cover-consideration store for tests (#2014).

Shared by the cover-consideration unit tests and the provider activation
integration test, so no test module imports another's fixtures.
"""

from __future__ import annotations

import json


class InMemoryCoverConsiderationStore:
    """The cover-consideration store calls, held in memory."""

    def __init__(self) -> None:
        self.current: dict[str, str] = {}
        self.events: list[dict[str, object]] = []

    def current_cover_consideration(self, review_case_id: str, replay_digest: str):
        document = self.current.get(review_case_id)
        if document is None or json.loads(document)["replay_digest"] != replay_digest:
            return None
        return document

    def current_cover_consideration_document(self, review_case_id: str):
        return self.current.get(review_case_id)

    def latest_cover_consideration_document(self, review_case_id: str):
        return self.current.get(review_case_id)

    def record_cover_consideration(self, document: str, **values):
        review_case_id = values["review_case_id"]
        previous = self.current.get(review_case_id)
        if previous is not None:
            self.events.append(
                {"kind": "superseded", "occurred_at": values["occurred_at"]}
            )
        self.current[review_case_id] = document
        self.events.append(
            {
                "kind": values.get("preparation_kind", "automatic_prepared"),
                "occurred_at": values["occurred_at"],
            }
        )
        return document

    def cover_consideration_history(self, review_case_id, limit=50):
        del review_case_id
        return tuple(reversed(self.events[-limit:]))
