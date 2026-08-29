"""Closed owner-facing preparation presentation vocabulary."""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.preparation_presentation import (
    PreparationActiveBook,
    PreparationCompletion,
    PreparationPassage,
    PreparationPresentation,
    PreparationRunState,
    PreparationTrigger,
    PreparationTriggerRecord,
)


class _IntSubclass(int):
    pass


@pytest.mark.parametrize("value", [True, _IntSubclass(1), -1])
@pytest.mark.parametrize("field", ["ready", "failed"])
def test_passage_rejects_noncanonical_outcome_counts(field: str, value: int) -> None:
    values = {
        "read": 2,
        "searches_finished": 2,
        "compared": 2,
        "checked": 2,
        "ready": 1,
        "failed": 1,
    }
    values[field] = value

    with pytest.raises(ValueError, match="passage is invalid"):
        PreparationPassage(**values)


def test_passage_rejects_later_stage_above_earlier_stage() -> None:
    with pytest.raises(ValueError, match="passage is unordered"):
        PreparationPassage(1, 2, 0, 0, 0, 0)


def _presentation(
    *,
    state: PreparationRunState = PreparationRunState.RUNNING,
    active: tuple[PreparationActiveBook, ...] = (),
    completions: tuple[PreparationCompletion, ...] = (),
    passage: PreparationPassage | None = None,
    total: int = 2,
) -> PreparationPresentation:
    return PreparationPresentation(
        run_id="preparation_run_" + "a" * 64,
        state=state,
        triggers=(
            PreparationTriggerRecord(
                PreparationTrigger.MANUAL_SCAN,
                1,
                100,
                100,
            ),
        ),
        passage=passage or PreparationPassage(2, 1, 1, 1, 1, 0),
        active_books=active,
        active_overflow=0,
        recent_completions=completions,
        throughput_per_minute=1.0,
        started_at=100,
        updated_at=101,
        completed_at=101
        if state
        in {
            PreparationRunState.COMPLETED_CLEAN,
            PreparationRunState.COMPLETED_WITH_FAILURES,
            PreparationRunState.DISMISSED,
        }
        else None,
        total=total,
    )


def test_presentation_bounds_private_book_lists() -> None:
    active = tuple(
        PreparationActiveBook(f"Book {index}", "Reading") for index in range(6)
    )
    completions = tuple(
        PreparationCompletion(f"Book {index}", "ready", 100 + index)
        for index in range(9)
    )

    with pytest.raises(ValueError, match="presentation is invalid"):
        _presentation(active=active)
    with pytest.raises(ValueError, match="presentation is invalid"):
        _presentation(completions=completions)


def test_only_completed_presentation_is_dismissible() -> None:
    assert not _presentation().dismissible
    assert _presentation(state=PreparationRunState.COMPLETED_CLEAN).dismissible
    assert _presentation(state=PreparationRunState.COMPLETED_WITH_FAILURES).dismissible


def test_failed_outcomes_are_independent_but_all_counts_are_bounded_by_total() -> None:
    early_failures = PreparationPassage(2, 1, 1, 0, 0, 2)
    assert _presentation(passage=early_failures).passage.failed == 2

    with pytest.raises(ValueError, match="presentation is invalid"):
        _presentation(passage=PreparationPassage(2, 1, 1, 1, 1, 2))
    with pytest.raises(ValueError, match="presentation is invalid"):
        _presentation(passage=PreparationPassage(3, 1, 1, 1, 1, 0), total=2)
