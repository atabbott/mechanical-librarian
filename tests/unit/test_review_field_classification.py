"""Closed field-classification matrix for #598 review projections."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    ReviewAttentionSnapshot,
    ReviewWebError,
)
from mechanical_librarian.domain.review_field_classification import (
    classify_review_field,
)


class ReviewFieldClassificationTests(unittest.TestCase):
    def test_closed_matrix_keeps_attention_and_recorded_states_distinct(self) -> None:
        cases = (
            ({"attention_candidate": True, "automatic_outcome": None, "has_human_decision": False, "suspended": False}, "needs_attention", True, True),
            ({"attention_candidate": False, "automatic_outcome": "abstention", "has_human_decision": False, "suspended": False}, "manual_fallback", True, True),
            ({"attention_candidate": True, "automatic_outcome": "candidate", "has_human_decision": False, "suspended": False}, "automatic_recorded", False, True),
            ({"attention_candidate": True, "automatic_outcome": None, "has_human_decision": True, "suspended": False}, "human_recorded", False, False),
            ({"attention_candidate": True, "automatic_outcome": None, "has_human_decision": False, "suspended": True}, "unsupported", False, False),
        )
        for inputs, state, attention, editable in cases:
            with self.subTest(state=state):
                value = classify_review_field(
                    **inputs, current_value="Title", proposed_values=("Title",),
                )
                self.assertEqual(value.state, state)
                self.assertEqual(value.needs_attention, attention)
                self.assertEqual(value.editable, editable)
                self.assertTrue(value.preservation_only)

    def test_owner_guidance_is_distinct_for_each_typed_unavailable_cause(self) -> None:
        expected = {
            "unsupported": "This field is unavailable for the current service capability.",
            "automatic_safety_suspended": "Automatic handling is suspended pending a safety recovery.",
            "external_field_failure": "This field is waiting for an external service recovery.",
            "external_connection_failure": "This connection is waiting for an external service recovery.",
            "external_recovered": "External recovery completed; this field can be reviewed again.",
        }
        for cause, guidance in expected.items():
            value = classify_review_field(attention_candidate=False, automatic_outcome=None, has_human_decision=False, suspended=cause != "unsupported", cause=cause, current_value=None, proposed_values=())
            self.assertEqual(value.reason, guidance)

    def test_canonical_proposal_values_compare_the_underlying_scalar_and_structure(self) -> None:
        for current, proposed in (
            ("Title", ({"kind": "text", "value": "Title"},)),
            ({"name": "Atlas", "sequence": "2"}, ({"kind": "series", "value": {"name": "Atlas", "sequence": "2"}},)),
        ):
            value = classify_review_field(
                attention_candidate=True, automatic_outcome=None,
                has_human_decision=False, suspended=False,
                current_value=current, proposed_values=proposed,
            )
            self.assertTrue(value.preservation_only)

    def test_attention_continues_after_two_contradictory_batches(self) -> None:
        class Repository:
            def __init__(self) -> None:
                self.offsets: list[int] = []

            def read_review_attention(self, search, limit, offset):
                self.offsets.append(offset)
                rows = tuple(("case", "report", f"invalid-{index}", 1)
                             for index in range(offset, offset + limit))
                return ReviewAttentionSnapshot(rows, 99)

        repository = Repository()
        browse = BrowseReviewLibrary(repository)  # type: ignore[arg-type]
        with patch(
            "mechanical_librarian.application.review_web._attention_item",
            return_value=None,
        ):
            page = browse.attention(search=None, limit=1, cursor=None)
        self.assertEqual(page["items"], [])
        self.assertIsNotNone(page["next_cursor"])
        self.assertEqual(repository.offsets, [0, 2])

    def test_attention_fetch_failure_remains_unavailable(self) -> None:
        class Repository:
            def read_review_attention(self, search, limit, offset):
                raise RuntimeError("offline")

        with self.assertRaisesRegex(ReviewWebError, "unavailable"):
            BrowseReviewLibrary(Repository()).attention(  # type: ignore[arg-type]
                search=None, limit=1, cursor=None,
            )

    def test_attention_backfills_a_valid_successor_and_ends_honestly(self) -> None:
        rows = (
            ("case", "report", "invalid", 1),
            ("case", "report", "also-invalid", 1),
            ("case", "report", "valid", 1),
        )

        class Repository:
            def read_review_attention(self, search, limit, offset):
                return ReviewAttentionSnapshot(rows[offset:offset + limit], len(rows))

        browse = BrowseReviewLibrary(Repository())  # type: ignore[arg-type]
        with patch(
            "mechanical_librarian.application.review_web._attention_item",
            side_effect=lambda case, report, destination, count: (
                None if destination != "valid" else {"destination": destination}
            ),
        ):
            page = browse.attention(search=None, limit=1, cursor=None)
        self.assertEqual(page["items"], [{"destination": "valid"}])
        self.assertIsNone(page["next_cursor"])

    def test_attention_last_valid_raw_row_has_no_cursor_and_no_stale_total(self) -> None:
        rows = (("case", "report", "stale", 1), ("case", "report", "valid", 1))

        class Repository:
            def read_review_attention(self, search, limit, offset):
                return ReviewAttentionSnapshot(rows[offset:offset + limit], len(rows))

        with patch(
            "mechanical_librarian.application.review_web._attention_item",
            side_effect=lambda case, report, destination, count: None if destination == "stale" else {"destination": destination},
        ):
            page = BrowseReviewLibrary(Repository()).attention(search=None, limit=1, cursor=None)  # type: ignore[arg-type]
        self.assertEqual(page["items"], [{"destination": "valid"}])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(page["total"], 1)


if __name__ == "__main__":
    unittest.main()
