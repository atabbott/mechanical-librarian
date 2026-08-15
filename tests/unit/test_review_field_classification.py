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

    def test_automatic_record_and_abstention_precede_later_safety_projection(self) -> None:
        for automatic_outcome, state, attention in (
            ("candidate", "automatic_recorded", False),
            ("abstention", "manual_fallback", True),
        ):
            value = classify_review_field(
                attention_candidate=True,
                automatic_outcome=automatic_outcome,
                has_human_decision=False,
                suspended=False,
                cause="automatic_safety_suspended",
                current_value="Current",
                proposed_values=("Candidate",),
            )
            self.assertEqual(state, value.state)
            self.assertEqual(attention, value.needs_attention)

    def test_advanced_automatic_effect_is_inspect_only(self) -> None:
        value = classify_review_field(
            attention_candidate=True,
            automatic_outcome="candidate",
            automatic_override_available=False,
            has_human_decision=False,
            suspended=False,
            current_value="Current",
            proposed_values=("Candidate",),
        )
        self.assertEqual("automatic_recorded", value.state)
        self.assertFalse(value.editable)
        self.assertIn("can no longer be overridden", value.reason)

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
        browse = BrowseReviewLibrary(repository)
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
            BrowseReviewLibrary(Repository()).attention(
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

        browse = BrowseReviewLibrary(Repository())
        with patch(
            "mechanical_librarian.application.review_web._attention_item",
            side_effect=lambda case, report, destination, count, **_kwargs: (
                None if destination != "valid" else {"destination": destination}
            ),
        ):
            page = browse.attention(search=None, limit=1, cursor=None)
        self.assertEqual(page["items"], [{"destination": "valid"}])
        self.assertIsNone(page["next_cursor"])

    def test_attention_last_valid_raw_row_has_no_cursor_and_stable_raw_total(self) -> None:
        rows = (("case", "report", "stale", 1), ("case", "report", "valid", 1))

        class Repository:
            def read_review_attention(self, search, limit, offset):
                return ReviewAttentionSnapshot(rows[offset:offset + limit], len(rows))

        with patch(
            "mechanical_librarian.application.review_web._attention_item",
            side_effect=lambda case, report, destination, count, **_kwargs: None if destination == "stale" else {"destination": destination},
        ):
            page = BrowseReviewLibrary(Repository()).attention(search=None, limit=1, cursor=None)
        self.assertEqual(page["items"], [{"destination": "valid"}])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(page["total"], 2)

    def test_attention_exact_boundary_all_contradictory_rows_proves_exhaustion(self) -> None:
        rows = (
            ("case", "report", "stale-1", 1),
            ("case", "report", "stale-2", 1),
            ("case", "report", "stale-3", 1),
            ("case", "report", "stale-4", 1),
        )

        class Repository:
            def __init__(self) -> None:
                self.offsets: list[int] = []

            def read_review_attention(self, search, limit, offset):
                self.offsets.append(offset)
                return ReviewAttentionSnapshot(rows[offset:offset + limit], len(rows))

        repository = Repository()
        with patch("mechanical_librarian.application.review_web._attention_item", return_value=None):
            page = BrowseReviewLibrary(repository).attention(search=None, limit=1, cursor=None)
        self.assertEqual([], page["items"])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(4, page["total"])
        self.assertEqual([0, 2], repository.offsets)

    def test_attention_terminal_contradictory_lookahead_has_no_empty_successor(self) -> None:
        rows = (("case", "report", "valid", 1), ("case", "report", "invalid", 1))

        class Repository:
            def read_review_attention(self, search, limit, offset):
                return ReviewAttentionSnapshot(rows[offset:offset + limit], len(rows))

        with patch(
            "mechanical_librarian.application.review_web._attention_item",
            side_effect=lambda case, report, destination, count, **_kwargs: (
                None if destination == "invalid" else {"destination": destination}
            ),
        ):
            page = BrowseReviewLibrary(Repository()).attention(search=None, limit=1, cursor=None)
        self.assertEqual([{"destination": "valid"}], page["items"])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(2, page["total"])

    def test_attention_visible_successor_remains_at_next_cursor(self) -> None:
        rows = (("case", "report", "valid-1", 1), ("case", "report", "invalid", 1), ("case", "report", "valid-2", 1))

        class Repository:
            def __init__(self) -> None:
                self.offsets: list[int] = []

            def read_review_attention(self, search, limit, offset):
                self.offsets.append(offset)
                return ReviewAttentionSnapshot(rows[offset:offset + limit], len(rows))

        def item(case, report, destination, count, **_kwargs):
            return None if destination == "invalid" else {"destination": destination}

        repository = Repository()
        browse = BrowseReviewLibrary(repository)
        with patch("mechanical_librarian.application.review_web._attention_item", side_effect=item):
            first = browse.attention(search=None, limit=1, cursor=None)
            second = browse.attention(search=None, limit=1, cursor=first["next_cursor"])
        self.assertEqual([{"destination": "valid-1"}], first["items"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(3, first["total"])
        self.assertEqual([{"destination": "valid-2"}], second["items"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(3, second["total"])
        self.assertEqual([0, 2, 2], repository.offsets)


if __name__ == "__main__":
    unittest.main()
