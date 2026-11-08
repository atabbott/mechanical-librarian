"""Closed field-classification matrix for #598 review projections."""

from __future__ import annotations

import re
from typing import get_args
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
    ReviewFieldCause,
    ReviewFieldClassificationCause,
    classify_review_field,
)
from tests.support.review_cases import _report


@pytest.fixture
def attention_documents():
    report = _report()
    case = ReviewCase.create(evaluate_report(report), report)
    return case.to_json(), report.to_json()


@pytest.mark.parametrize(
    ("inputs", "state", "cause", "attention", "editable"),
    (
        (
            {
                "attention_candidate": True,
                "automatic_outcome": None,
                "has_human_decision": False,
                "suspended": False,
            },
            "needs_attention",
            "review_needed",
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
            "automatic_abstained",
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
            "automatic_decided",
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
            "human_decided",
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
            "automatic_safety_suspended",
            False,
            False,
        ),
        (
            {
                "attention_candidate": False,
                "automatic_outcome": None,
                "has_human_decision": False,
                "suspended": False,
                "owner_choosable": True,
            },
            "owner_choosable",
            "owner_choosable",
            False,
            True,
        ),
    ),
    ids=(
        "needs_attention",
        "manual_fallback",
        "automatic_recorded",
        "human_recorded",
        "unsupported",
        "owner_choosable",
    ),
)
def test_closed_matrix_keeps_attention_and_recorded_states_distinct(
    inputs: dict[str, object], state: str, cause: str, attention: bool, editable: bool
) -> None:
    value = classify_review_field(
        **inputs,
        current_value="Title",
        proposed_values=("Title",),
    )
    assert value.state == state
    assert value.cause == cause
    assert value.needs_attention == attention
    assert value.editable == editable
    assert value.preservation_only


_BASE_INPUTS: dict[str, object] = {
    "attention_candidate": False,
    "automatic_outcome": None,
    "has_human_decision": False,
    "suspended": False,
    "cause": None,
}


@pytest.mark.parametrize(
    ("higher", "state", "cause"),
    (
        ({"attention_candidate": True}, "needs_attention", "review_needed"),
        ({"automatic_outcome": "abstention"}, "manual_fallback", "automatic_abstained"),
        ({"automatic_outcome": "candidate"}, "automatic_recorded", "automatic_decided"),
        ({"has_human_decision": True}, "human_recorded", "human_decided"),
        (
            {"suspended": True, "cause": "external_field_failure"},
            "unsupported",
            "external_field_failure",
        ),
        (
            {"cause": "automatic_safety_suspended"},
            "needs_attention",
            "automatic_safety_suspended",
        ),
        ({"cause": "unsupported"}, "unsupported", "unsupported"),
    ),
    ids=(
        "attention",
        "abstention",
        "candidate",
        "human",
        "suspended",
        "safety",
        "unsupported",
    ),
)
def test_every_other_input_decides_before_owner_choosable(
    higher: dict[str, object], state: str, cause: str
) -> None:
    """An optional choice is only what is left when nothing else applies."""
    inputs = {**_BASE_INPUTS, **higher}
    plain = classify_review_field(
        **inputs, current_value="Title", proposed_values=("Other",)
    )
    choosable = classify_review_field(
        **inputs,
        current_value="Title",
        proposed_values=("Other",),
        owner_choosable=True,
    )

    assert plain.state == choosable.state == state
    assert plain.cause == choosable.cause == cause
    assert plain == choosable


def test_owner_choosable_is_an_uncounted_inspectable_choice() -> None:
    value = classify_review_field(
        **_BASE_INPUTS,
        current_value="Title",
        proposed_values=("Other",),
        owner_choosable=True,
    )

    assert value.navigation_role == "inspect"
    assert value.cause == "owner_choosable"
    assert value.recorded is False
    assert value.preservation_only is False
    # The browser's needs-review filter must never read this reason.
    assert not re.search(r"needs (?:your )?review", value.reason, re.IGNORECASE)


def test_an_immaterial_optional_choice_says_why_no_review_is_needed() -> None:
    """One statement explains an optional choice beside its conditions (#1450)."""
    plain, immaterial = (
        classify_review_field(
            **_BASE_INPUTS,
            current_value="Title",
            proposed_values=("Other",),
            owner_choosable=True,
            immaterial_alternatives=flag,
        )
        for flag in (False, True)
    )

    assert plain.reason == (
        "No review is needed. You can still keep the current value or "
        "choose a proposed one."
    )
    assert immaterial.reason == (
        "The proposed values only shorten or extend the current one, so no "
        "review is needed. You can still keep the current value or choose a "
        "proposed one."
    )
    assert immaterial == plain.__class__(
        plain.state,
        immaterial.reason,
        plain.cause,
        plain.needs_attention,
        plain.editable,
        plain.recorded,
        plain.navigation_role,
        plain.preservation_only,
    )
    kept = classify_review_field(
        **_BASE_INPUTS,
        current_value="cover",
        proposed_values=("other",),
        owner_choosable=True,
        kept_cover=True,
    )
    assert kept.reason == (
        "Your current cover is kept, so no review is needed. You can still "
        "choose another cover."
    )
    assert (kept.state, kept.cause, kept.needs_attention, kept.editable) == (
        plain.state,
        "owner_choosable",
        False,
        True,
    )
    # Every state that decides first keeps its own reason.
    assert (
        classify_review_field(
            **{**_BASE_INPUTS, "attention_candidate": True},
            current_value="Title",
            proposed_values=("Other",),
            owner_choosable=True,
            immaterial_alternatives=True,
        ).reason
        == "This field needs your review."
    )


@pytest.mark.parametrize(
    ("flag", "reason"),
    (
        (
            "kept_by_fill_policy",
            "Your setting only fills empty fields, so the current value is kept "
            "and no review is needed. You can still choose a proposed one.",
        ),
        (
            "kept_by_tags_off",
            "Tags are off in your settings, so your tags are left as they are "
            "and no review is needed. You can still choose proposed tags.",
        ),
        (
            "single_source",
            "Only one source suggests this, and nothing else confirms it, so it "
            "isn't used and no review is needed. You can still choose it.",
        ),
    ),
)
def test_a_choice_the_policy_settles_says_the_value_is_kept(
    flag: str, reason: str
) -> None:
    """The setting that keeps the current value is named only when optional (#1310)."""
    optional = classify_review_field(
        **_BASE_INPUTS,
        current_value="Title",
        proposed_values=("Other",),
        owner_choosable=True,
        **{flag: True},
    )
    assert optional.state == "owner_choosable"
    assert optional.cause == "owner_choosable"
    assert optional.needs_attention is False
    assert optional.reason == reason
    assert not re.search(r"verified|confirmed", optional.reason, re.IGNORECASE)


@pytest.mark.parametrize(
    ("cause", "guidance"),
    (
        (
            "unsupported",
            "Your library service cannot change this field.",
        ),
        (
            "automatic_safety_suspended",
            "Automatic is paused, so this field is waiting for you.",
        ),
        (
            "external_field_failure",
            "This field is waiting for the library to accept changes to it again.",
        ),
        (
            "external_connection_failure",
            "This field is waiting for the library connection to recover.",
        ),
        (
            "external_recovered",
            "The library connection recovered. You can review this field again.",
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
    assert value.cause == cause


@pytest.mark.parametrize(
    ("inputs", "state", "cause"),
    (
        ({"suspended": True}, "unsupported", "automatic_safety_suspended"),
        ({}, "unsupported", "nothing_to_decide"),
        ({"owner_entry": True}, "unsupported", "nothing_to_decide"),
        (
            {"automatic_outcome": "candidate", "override_available": False},
            "automatic_recorded",
            "automatic_decided",
        ),
        (
            {"has_human_decision": True, "override_available": False},
            "human_recorded",
            "human_decided",
        ),
        (
            # A cause a higher branch overrides does not leak into the code.
            {
                "attention_candidate": True,
                "cause": "external_connection_failure",
            },
            "needs_attention",
            "review_needed",
        ),
        (
            {"has_human_decision": True, "cause": "automatic_safety_suspended"},
            "human_recorded",
            "human_decided",
        ),
    ),
    ids=(
        "suspended-without-cause",
        "fallback",
        "owner-entry-fallback",
        "automatic-override-blocked",
        "human-override-blocked",
        "ignored-cause",
        "human-over-safety",
    ),
)
def test_the_code_names_the_branch_that_chose_the_sentence(
    inputs: dict[str, object], state: str, cause: str
) -> None:
    """Library reads the code, never the sentence, so each branch names its own (#1791)."""
    value = classify_review_field(
        **{**_BASE_INPUTS, **inputs},
        current_value="Title",
        proposed_values=("Other",),
    )
    assert (value.state, value.cause) == (state, cause)


def test_the_code_list_holds_every_input_cause() -> None:
    assert set(get_args(ReviewFieldCause)) <= set(
        get_args(ReviewFieldClassificationCause)
    )
    assert len(set(get_args(ReviewFieldClassificationCause))) == 11


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
    assert "cannot be changed yet" in value.reason


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
