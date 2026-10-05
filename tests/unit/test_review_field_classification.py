"""Closed field-classification matrix for #598 review projections."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    ReviewAttentionSnapshot,
    ReviewWebError,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.review_field_classification import (
    classify_review_field,
)
from tests.support.review_cases import _report


@pytest.fixture
def attention_documents():
    report = _report()
    case = ReviewCase.create(evaluate_report(report), report)
    return case.to_json(), report.to_json()


@pytest.mark.parametrize(
    ("inputs", "state", "attention", "editable"),
    (
        (
            {
                "attention_candidate": True,
                "automatic_outcome": None,
                "has_human_decision": False,
                "suspended": False,
            },
            "needs_attention",
            True,
            True,
        ),
        (
            {
                "attention_candidate": False,
                "automatic_outcome": "abstention",
                "has_human_decision": False,
                "suspended": False,
            },
            "manual_fallback",
            True,
            True,
        ),
        (
            {
                "attention_candidate": True,
                "automatic_outcome": "candidate",
                "has_human_decision": False,
                "suspended": False,
            },
            "automatic_recorded",
            False,
            True,
        ),
        (
            {
                "attention_candidate": True,
                "automatic_outcome": None,
                "has_human_decision": True,
                "suspended": False,
            },
            "human_recorded",
            False,
            True,
        ),
        (
            {
                "attention_candidate": True,
                "automatic_outcome": None,
                "has_human_decision": False,
                "suspended": True,
            },
            "unsupported",
            False,
            False,
        ),
    ),
    ids=(
        "needs_attention",
        "manual_fallback",
        "automatic_recorded",
        "human_recorded",
        "unsupported",
    ),
)
def test_closed_matrix_keeps_attention_and_recorded_states_distinct(
    inputs: dict[str, object], state: str, attention: bool, editable: bool
) -> None:
    value = classify_review_field(
        **inputs,
        current_value="Title",
        proposed_values=("Title",),
    )
    assert value.state == state
    assert value.needs_attention == attention
    assert value.editable == editable
    assert value.preservation_only


@pytest.mark.parametrize(
    ("cause", "guidance"),
    (
        (
            "unsupported",
            "This field is unavailable for the current service capability.",
        ),
        (
            "automatic_safety_suspended",
            "Automatic handling is suspended pending a safety recovery.",
        ),
        (
            "external_field_failure",
            "This field is waiting for an external service recovery.",
        ),
        (
            "external_connection_failure",
            "This connection is waiting for an external service recovery.",
        ),
        (
            "external_recovered",
            "External recovery completed; this field can be reviewed again.",
        ),
    ),
)
def test_owner_guidance_is_distinct_for_each_typed_unavailable_cause(
    cause: str, guidance: str
) -> None:
    value = classify_review_field(
        attention_candidate=False,
        automatic_outcome=None,
        has_human_decision=False,
        suspended=cause != "unsupported",
        cause=cause,
        current_value=None,
        proposed_values=(),
    )
    assert value.reason == guidance


@pytest.mark.parametrize(
    ("current", "proposed"),
    (
        ("Title", ({"kind": "text", "value": "Title"},)),
        (
            {"name": "Atlas", "sequence": "2"},
            ({"kind": "series", "value": {"name": "Atlas", "sequence": "2"}},),
        ),
    ),
    ids=("text", "series"),
)
def test_canonical_proposal_values_compare_the_underlying_scalar_and_structure(
    current: object, proposed: tuple[dict[str, object], ...]
) -> None:
    value = classify_review_field(
        attention_candidate=True,
        automatic_outcome=None,
        has_human_decision=False,
        suspended=False,
        current_value=current,
        proposed_values=proposed,
    )
    assert value.preservation_only


@pytest.mark.parametrize(
    ("automatic_outcome", "state", "attention"),
    (
        ("candidate", "automatic_recorded", False),
        ("abstention", "manual_fallback", True),
    ),
    ids=("candidate", "abstention"),
)
def test_automatic_record_and_abstention_precede_later_safety_projection(
    automatic_outcome: str, state: str, attention: bool
) -> None:
    value = classify_review_field(
        attention_candidate=True,
        automatic_outcome=automatic_outcome,
        has_human_decision=False,
        suspended=False,
        cause="automatic_safety_suspended",
        current_value="Current",
        proposed_values=("Candidate",),
    )
    assert state == value.state
    assert attention == value.needs_attention


def test_advanced_automatic_effect_is_inspect_only() -> None:
    value = classify_review_field(
        attention_candidate=True,
        automatic_outcome="candidate",
        override_available=False,
        has_human_decision=False,
        suspended=False,
        current_value="Current",
        proposed_values=("Candidate",),
    )
    assert "automatic_recorded" == value.state
    assert not value.editable
    # A verified automatic effect is inspect-only while its authority is still
    # being resolved; it may become replaceable again once that state settles.
    assert "cannot be overridden yet" in value.reason


def test_attention_continues_after_two_contradictory_batches(
    attention_documents,
) -> None:
    class Repository:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def read_review_attention(self, search, limit, offset, library_labels=()):
            del library_labels  # These fixtures isolate pagination and read failure.
            self.offsets.append(offset)
            rows = tuple(
                (*attention_documents, f"invalid-{index}", 1, None, None, False)
                for index in range(offset, offset + limit)
            )
            return ReviewAttentionSnapshot(rows, 99)

    repository = Repository()
    browse = BrowseReviewLibrary(repository)
    with patch(
        "mechanical_librarian.application.review_web._attention_item",
        return_value=None,
    ):
        page = browse.attention(search=None, limit=1, cursor=None)
    assert page["items"] == []
    assert page["next_cursor"] is not None
    assert repository.offsets == [0, 2]


def test_attention_fetch_failure_remains_unavailable() -> None:
    class Repository:
        def read_review_attention(self, search, limit, offset, library_labels=()):
            del library_labels  # These fixtures isolate pagination and read failure.
            raise RuntimeError("offline")

    with pytest.raises(ReviewWebError, match="unavailable") as failure:
        BrowseReviewLibrary(Repository()).attention(
            search=None,
            limit=1,
            cursor=None,
        )
    assert type(failure.value.__cause__) is RuntimeError
    assert str(failure.value.__cause__) == "offline"


def test_attention_backfills_a_valid_successor_and_ends_honestly(
    attention_documents,
) -> None:
    rows = (
        (*attention_documents, "invalid", 1, None, None, False),
        (*attention_documents, "also-invalid", 1, None, None, False),
        (*attention_documents, "valid", 1, None, None, False),
    )

    class Repository:
        def read_review_attention(self, search, limit, offset, library_labels=()):
            del library_labels  # These fixtures isolate pagination and read failure.
            return ReviewAttentionSnapshot(rows[offset : offset + limit], len(rows))

    browse = BrowseReviewLibrary(Repository())
    with patch(
        "mechanical_librarian.application.review_web._attention_item",
        side_effect=lambda case, report, destination, count, **_kwargs: (
            None if destination != "valid" else {"destination": destination}
        ),
    ):
        page = browse.attention(search=None, limit=1, cursor=None)
    assert page["items"] == [{"destination": "valid"}]
    assert page["next_cursor"] is None


def test_attention_last_valid_raw_row_has_no_cursor_and_stable_raw_total(
    attention_documents,
) -> None:
    rows = (
        (*attention_documents, "stale", 1, None, None, False),
        (*attention_documents, "valid", 1, None, None, False),
    )

    class Repository:
        def read_review_attention(self, search, limit, offset, library_labels=()):
            del library_labels  # These fixtures isolate pagination and read failure.
            return ReviewAttentionSnapshot(rows[offset : offset + limit], len(rows))

    with patch(
        "mechanical_librarian.application.review_web._attention_item",
        side_effect=lambda case, report, destination, count, **_kwargs: (
            None if destination == "stale" else {"destination": destination}
        ),
    ):
        page = BrowseReviewLibrary(Repository()).attention(
            search=None, limit=1, cursor=None
        )
    assert page["items"] == [{"destination": "valid"}]
    assert page["next_cursor"] is None
    assert page["total"] == 2


def test_attention_exact_boundary_all_contradictory_rows_proves_exhaustion(
    attention_documents,
) -> None:
    rows = (
        (*attention_documents, "stale-1", 1, None, None, False),
        (*attention_documents, "stale-2", 1, None, None, False),
        (*attention_documents, "stale-3", 1, None, None, False),
        (*attention_documents, "stale-4", 1, None, None, False),
    )

    class Repository:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def read_review_attention(self, search, limit, offset, library_labels=()):
            del library_labels  # These fixtures isolate pagination and read failure.
            self.offsets.append(offset)
            return ReviewAttentionSnapshot(rows[offset : offset + limit], len(rows))

    repository = Repository()
    with patch(
        "mechanical_librarian.application.review_web._attention_item", return_value=None
    ):
        page = BrowseReviewLibrary(repository).attention(
            search=None, limit=1, cursor=None
        )
    assert [] == page["items"]
    assert page["next_cursor"] is None
    assert 4 == page["total"]
    assert [0, 2] == repository.offsets


def test_attention_terminal_contradictory_lookahead_has_no_empty_successor(
    attention_documents,
) -> None:
    rows = (
        (*attention_documents, "valid", 1, None, None, False),
        (*attention_documents, "invalid", 1, None, None, False),
    )

    class Repository:
        def read_review_attention(self, search, limit, offset, library_labels=()):
            del library_labels  # These fixtures isolate pagination and read failure.
            return ReviewAttentionSnapshot(rows[offset : offset + limit], len(rows))

    with patch(
        "mechanical_librarian.application.review_web._attention_item",
        side_effect=lambda case, report, destination, count, **_kwargs: (
            None if destination == "invalid" else {"destination": destination}
        ),
    ):
        page = BrowseReviewLibrary(Repository()).attention(
            search=None, limit=1, cursor=None
        )
    assert [{"destination": "valid"}] == page["items"]
    assert page["next_cursor"] is None
    assert 2 == page["total"]


def test_attention_visible_successor_remains_at_next_cursor(
    attention_documents,
) -> None:
    rows = (
        (*attention_documents, "valid-1", 1, None, None, False),
        (*attention_documents, "invalid", 1, None, None, False),
        (*attention_documents, "valid-2", 1, None, None, False),
    )

    class Repository:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def read_review_attention(self, search, limit, offset, library_labels=()):
            del library_labels  # These fixtures isolate pagination and read failure.
            self.offsets.append(offset)
            return ReviewAttentionSnapshot(rows[offset : offset + limit], len(rows))

    def item(case, report, destination, count, **_kwargs):
        return None if destination == "invalid" else {"destination": destination}

    repository = Repository()
    browse = BrowseReviewLibrary(repository)
    with patch(
        "mechanical_librarian.application.review_web._attention_item", side_effect=item
    ):
        first = browse.attention(search=None, limit=1, cursor=None)
        second = browse.attention(search=None, limit=1, cursor=first["next_cursor"])
    assert [{"destination": "valid-1"}] == first["items"]
    assert first["next_cursor"] is not None
    assert 3 == first["total"]
    assert [{"destination": "valid-2"}] == second["items"]
    assert second["next_cursor"] is None
    assert 3 == second["total"]
    assert [0, 2, 2] == repository.offsets
