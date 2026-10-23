from __future__ import annotations

import json

import pytest

from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    OwnerSuggestion,
    admitted_applications,
    decision_actions,
    list_union,
    owner_text,
    owner_value_suggestions,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.genre_reports import genre_report
from tests.support.review_cases import _report


@pytest.fixture
def case() -> ReviewCase:
    report = _report()
    return ReviewCase.create(evaluate_report(report), report)


@pytest.fixture
def proposal(case: ReviewCase) -> dict[str, object]:
    return next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )


def _create(
    case: ReviewCase,
    proposal: dict[str, object],
    outcome: str,
    selected_value: dict[str, object] | None,
    *,
    key: str = "domain-decision-0001",
) -> HumanDecision:
    return HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome=outcome,
        selected_value=selected_value,
        idempotency_key=key,
        occurred_at=1_000,
    )


_PLAN = "effect_plan_" + "e" * 64


@pytest.mark.parametrize(
    "outcome",
    ("accept", "keep_current", "reject", "owner_entered", "withdraw", "revert"),
)
def test_all_six_outcomes_are_canonical_and_exact_version_bound(
    case: ReviewCase, proposal: dict[str, object], outcome: str
) -> None:
    value = {
        "accept": proposal["result"]["alternatives"][0]["value"],
        "owner_entered": {"kind": "text", "value": "The Silent Orchard"},
        "revert": {"kind": "text", "value": "An Earlier Title"},
    }.get(outcome)
    decision = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome=outcome,
        selected_value=value,
        idempotency_key=f"domain-{outcome}-decision",
        occurred_at=1_000,
        reverts_plan_id=_PLAN if outcome == "revert" else None,
    )
    assert HumanDecision.from_json(decision.to_json()) == decision
    assert json.loads(decision.to_json())["schema_version"] == "human-decision-v3"
    decision.validate_for(case)
    decision.validate_new_for(case)


def _owner(
    case: ReviewCase,
    destination: str,
    value: object,
    *,
    kind: str = "text",
    outcome: str = "owner_entered",
) -> HumanDecision:
    proposal = next(
        value for value in case.proposals if value["destination"] == destination
    )
    return HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=destination,
        actor="owner",
        outcome=outcome,
        selected_value=None if value is None else {"kind": kind, "value": value},
        idempotency_key="domain-owner-value-0001",
        occurred_at=1_000,
    )


@pytest.mark.parametrize(
    ("value", "code"),
    (
        ("The Silent Orchard", None),
        ("x" * 512, None),
        ("x" * 513, "owner_value_invalid"),
        ("", "owner_value_invalid"),
        ("The\u0000Orchard", "owner_value_invalid"),
        ("The\u200bOrchard", "owner_value_invalid"),
        (" The Silent Orchard", "owner_value_invalid"),
        ("The  Silent Orchard", "owner_value_invalid"),
        ("Cafe\u0301 Society", "owner_value_invalid"),
        ("https://example.invalid/book", "owner_value_invalid"),
        ("/srv/library/book.epub", "owner_value_invalid"),
        ("C:\\Books\\book", "owner_value_invalid"),
        ("~/Books/title", "owner_value_invalid"),
        ("Title", "owner_value_unchanged"),
        (17, "owner_value_invalid"),
    ),
)
def test_owner_entered_text_is_canonical_safe_and_a_change(
    case: ReviewCase, value: object, code: str | None
) -> None:
    if code is None:
        decision = _owner(case, "title", value)
        assert decision.selected_value == {"kind": "text", "value": value}
        return
    with pytest.raises(HumanDecisionError) as refused:
        _owner(case, "title", value)
    assert refused.value.code == code


def test_owner_entry_is_admitted_only_for_its_destinations_and_eligible_proposals(
    case: ReviewCase,
) -> None:
    # Publisher is missing here, so its proposal is ineligible; ISBN is never
    # free-form; a withdraw carries no value and refuses cover and description.
    for destination, value, outcome, code in (
        ("publisher", "Harbour Press", "owner_entered", "unsupported"),
        ("isbn", "9780306406157", "owner_entered", "unsupported"),
        ("description", None, "withdraw", "unsupported"),
        ("cover", None, "withdraw", "unsupported"),
    ):
        with pytest.raises(HumanDecisionError) as refused:
            _owner(case, destination, value, outcome=outcome)
        assert refused.value.code == code
    with pytest.raises(HumanDecisionError) as wrong_kind:
        _owner(case, "title", ["The Silent Orchard"], kind="list")
    assert wrong_kind.value.code == "owner_value_invalid"


def test_v3_requires_exactly_its_keys_and_binds_the_reverted_plan(
    case: ReviewCase, proposal: dict[str, object]
) -> None:
    revert = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="revert",
        selected_value={"kind": "text", "value": "An Earlier Title"},
        idempotency_key="domain-revert-binding-01",
        occurred_at=1_000,
        reverts_plan_id=_PLAN,
    )
    other = HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="revert",
        selected_value={"kind": "text", "value": "An Earlier Title"},
        idempotency_key="domain-revert-binding-01",
        occurred_at=1_000,
        reverts_plan_id="effect_plan_" + "f" * 64,
    )
    assert revert.request_digest != other.request_digest
    assert not revert.matches_request(
        review_case_id=case.review_case_id,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="revert",
        selected_value={"kind": "text", "value": "An Earlier Title"},
        idempotency_key="domain-revert-binding-01",
        reverts_plan_id=other.reverts_plan_id,
    )
    raw = json.loads(revert.to_json())
    for tampered in (
        {**raw, "schema_version": "human-decision-v2"},
        {key: value for key, value in raw.items() if key != "reverts_plan_id"},
        {**raw, "reverts_plan_id": None},
        {**raw, "selected_value": None},
    ):
        with pytest.raises(HumanDecisionError):
            HumanDecision.from_json(
                json.dumps(tampered, separators=(",", ":"), sort_keys=True)
            )
    for outcome, plan in (
        ("revert", None),
        ("revert", "effect_plan_short"),
        ("owner_entered", _PLAN),
    ):
        with pytest.raises(HumanDecisionError) as invalid:
            HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination="title",
                actor="owner",
                outcome=outcome,
                selected_value={"kind": "text", "value": "An Earlier Title"},
                idempotency_key="domain-revert-binding-02",
                occurred_at=1_000,
                reverts_plan_id=plan,
            )
        assert invalid.value.code == "invalid"
    with pytest.raises(HumanDecisionError) as shapeless:
        HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination="title",
            actor="owner",
            outcome="revert",
            selected_value={"kind": "list", "value": ["An Earlier Title"]},
            idempotency_key="domain-revert-binding-03",
            occurred_at=1_000,
            reverts_plan_id=_PLAN,
        )
    assert shapeless.value.code == "invalid_selection"


@pytest.fixture
def genre_case() -> ReviewCase:
    report = genre_report(
        genres=("Fantasy", "Mystery", "Romance"), current=("Drama", "Fantasy")
    )
    return ReviewCase.create(evaluate_report(report), report)


def test_genre_union_is_current_then_proposed_in_order(genre_case: ReviewCase) -> None:
    proposal = next(
        value for value in genre_case.proposals if value["destination"] == "genres"
    )
    assert list_union(proposal, ["Drama", "Fantasy"]) == (
        ("Drama", True, False),
        ("Fantasy", True, True),
        ("Mystery", False, True),
        ("Romance", False, True),
    )


@pytest.mark.parametrize(
    ("value", "code"),
    (
        (["Fantasy", "Mystery"], None),
        (["Romance"], None),
        (["Drama", "Fantasy", "Mystery", "Romance"], None),
        (["Mystery", "Fantasy"], "owner_value_invalid"),
        (["Fantasy", "Fantasy"], "owner_value_invalid"),
        (["Fantasy", "Horror"], "owner_value_invalid"),
        ([], "owner_value_invalid"),
        (["Drama", "Fantasy"], "owner_value_unchanged"),
        ([f"Genre {index}" for index in range(33)], "owner_value_invalid"),
        (["fantasy"], "owner_value_invalid"),
    ),
)
def test_genre_subset_is_a_nonempty_union_order_change(
    genre_case: ReviewCase, value: list[str], code: str | None
) -> None:
    if code is None:
        assert _owner(genre_case, "genres", value, kind="list").selected_value == {
            "kind": "list",
            "value": value,
        }
        return
    with pytest.raises(HumanDecisionError) as refused:
        _owner(genre_case, "genres", value, kind="list")
    assert refused.value.code == code


def test_owner_commands_leave_the_proposal_actions_unchanged(
    genre_case: ReviewCase,
) -> None:
    """F59 adds commands, never proposal choices, so attention entry is fixed."""
    actions = {
        value["destination"]: decision_actions(value) for value in genre_case.proposals
    }
    assert actions["title"] == ()
    assert actions["genres"] == ("keep_current", "accept")
    assert {destination for destination, value in actions.items() if value} == {
        "authors",
        "series",
        "genres",
        "description",
        "isbn",
        "tags",
    }


def test_invalid_selection_nonactionable_field_and_tampering_fail_closed(
    case: ReviewCase, proposal: dict[str, object]
) -> None:
    with pytest.raises(HumanDecisionError) as selection:
        _create(case, proposal, "accept", {"kind": "text", "value": "invented"})
    assert selection.value.code == "invalid_selection"
    resolved = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] in {"keep", "reject", "defer", "ineligible"}
        and value["manual_required"] is False
    )
    with pytest.raises(HumanDecisionError) as unsupported:
        HumanDecision.create(
            case,
            proposal_id=resolved["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=resolved["destination"],
            actor="owner",
            outcome="reject",
            selected_value=None,
            idempotency_key="domain-resolved-0001",
            occurred_at=1_000,
        )
    assert unsupported.value.code == "unsupported"
    decision = _create(case, proposal, "reject", None)
    tampered = json.loads(decision.to_json())
    tampered["destination"] = "subtitle"
    with pytest.raises(HumanDecisionError):
        HumanDecision.from_json(
            json.dumps(tampered, separators=(",", ":"), sort_keys=True)
        )


def test_retained_preservation_accept_remains_readable_without_reopening_command_admission(
    case: ReviewCase, proposal: dict[str, object]
) -> None:
    alternative = next(
        value
        for value in proposal["result"]["alternatives"]
        if value["assessment"] != "preserves_target"
    )
    selected = alternative["value"]
    decision = _create(case, proposal, "accept", selected)
    preserved = json.loads(json.dumps(proposal))
    for value in preserved["result"]["alternatives"]:
        value["assessment"] = "preserves_target"
    retained = type(
        "RetainedCase",
        (),
        {
            "review_case_id": case.review_case_id,
            "target_fingerprint": case.target_fingerprint,
            "proposals": (preserved,),
            "media_kind": case.media_kind,
        },
    )()

    decision.validate_for(retained)
    with pytest.raises(HumanDecisionError) as rejected:
        HumanDecision.create(
            retained,
            proposal_id=preserved["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=preserved["destination"],
            actor="owner",
            outcome="accept",
            selected_value=selected,
            idempotency_key="domain-preservation-new-0001",
            occurred_at=1_000,
        )
    assert rejected.value.code == "unsupported"


@pytest.mark.parametrize("outcome", ("keep_current", "reject"))
def test_retained_nonaccept_keeps_the_command_validator_error_identity(
    case: ReviewCase, proposal: dict[str, object], outcome: str
) -> None:
    decision = _create(case, proposal, outcome, None)
    resolved = json.loads(json.dumps(proposal))
    resolved["result"]["outcome"] = "keep"
    retained = type(
        "RetainedCase",
        (),
        {
            "review_case_id": case.review_case_id,
            "target_fingerprint": case.target_fingerprint,
            "proposals": (resolved,),
            "media_kind": case.media_kind,
        },
    )()

    with pytest.raises(HumanDecisionError) as rejected:
        decision.validate_for(retained)

    assert rejected.value.code == "unsupported"


@pytest.mark.parametrize(
    ("outcome", "manual_required", "alternatives", "expected"),
    (
        (
            "unresolved_choice",
            False,
            [{"value": {"kind": "text", "value": "new"}}],
            ("keep_current", "accept"),
        ),
        (
            "propose_change",
            False,
            [{"value": {"kind": "text", "value": "new"}}],
            ("keep_current", "accept"),
        ),
        ("acceptable_choice", False, [], ()),
        ("keep", True, [], ()),
        ("keep", False, [], ()),
        ("defer", False, [], ()),
        ("unresolved_choice", False, None, ()),
    ),
)
def test_decision_actions_fail_closed_to_the_exact_human_decision_contract(
    outcome: str,
    manual_required: bool,
    alternatives: object,
    expected: tuple[str, ...],
) -> None:
    proposal: dict[str, object] = {
        "destination": "title",
        "manual_required": manual_required,
        "result": {"outcome": outcome, "alternatives": alternatives},
    }
    assert decision_actions(proposal) == expected


def test_owner_can_replace_an_automatic_actionable_selection(
    case: ReviewCase, proposal: dict[str, object]
) -> None:
    resolved = json.loads(json.dumps(proposal))
    resolved["result"]["outcome"] = "propose_change"
    replacement = type(
        "ResolvedCase",
        (),
        {
            "review_case_id": case.review_case_id,
            "target_fingerprint": case.target_fingerprint,
            "proposals": (resolved,),
            "media_kind": case.media_kind,
        },
    )()
    selected = resolved["result"]["alternatives"][0]["value"]
    decision = HumanDecision.create(
        replacement,
        proposal_id=resolved["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=resolved["destination"],
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="domain-automatic-override-0001",
        occurred_at=1_000,
    )
    decision.validate_for(replacement)


def test_admitted_applications_names_the_retained_history_one_medium_may_carry() -> (
    None
):
    """Every decision formed since F47 says `apply`; only history says otherwise.

    A recording is the only case that may carry an F45 `recorded_only`
    document, and no medium admits one as a new command (ADR-0071, #968).
    """
    assert frozenset({"apply"}) == admitted_applications("ebook")
    assert frozenset({"apply", "recorded_only"}) == admitted_applications("audiobook")


_SUGGESTION_TABLE = (
    ("title", "THE SILENT ORCHARD", (), (("title_case", "The Silent Orchard"),)),
    ("title", "the harbour of glass", (), (("title_case", "The Harbour of Glass"),)),
    (
        "title",
        "VOLUME III: A WINTER FOR THE BRAVE",
        (),
        (("title_case", "Volume III: A Winter for the Brave"),),
    ),
    (
        "title",
        "well-kept secrets of the north",
        (),
        (("title_case", "Well-Kept Secrets of the North"),),
    ),
    ("title", "ÉTÉ À PARIS", (), (("title_case", "Été À Paris"),)),
    ("title", "iPhone Stories", (), ()),
    ("title", "東京物語", (), ()),
    ("title", "X", (), ()),
    (
        "title",
        "The Silent Orchard (Disc 2)",
        (),
        (("strip_part", "The Silent Orchard"),),
    ),
    (
        "title",
        "The Silent Orchard - Part 1 of 3",
        (),
        (("strip_part", "The Silent Orchard"),),
    ),
    ("title", "Harbour of Glass, CD 01", (), (("strip_part", "Harbour of Glass"),)),
    ("title", "A Winter Tale Pt. IV", (), (("strip_part", "A Winter Tale"),)),
    ("title", "Part 2", (), ()),
    ("title", "Partition Lines", (), ()),
    ("title", "The Second Part", (), ()),
    ("title", "Disc 2: The Return", (), ()),
    ("title", "Chapter 3", (), ()),
    (
        "title",
        "Elena Marsh - The Silent Orchard",
        ("Elena Marsh",),
        (("split_author", "The Silent Orchard"),),
    ),
    (
        "title",
        "The Silent Orchard \u2014 ELENA MARSH",
        ("Elena Marsh",),
        (("split_author", "The Silent Orchard"),),
    ),
    ("title", "Spider-Man Returns", ("Elena Marsh",), ()),
    ("title", "A - B - C", ("A",), ()),
    (
        "title",
        "THE SILENT ORCHARD (Disc 2)",
        (),
        (("strip_part", "THE SILENT ORCHARD"), ("combined", "The Silent Orchard")),
    ),
    # Subtitle takes two rules and publisher one, whatever else would apply.
    (
        "subtitle",
        "a novel (part 2)",
        (),
        (("title_case", "A Novel (Part 2)"), ("strip_part", "a novel")),
    ),
    ("publisher", "HARBOUR PRESS (Disc 2)", (), ()),
    ("publisher", "HARBOUR PRESS", (), (("title_case", "Harbour Press"),)),
    ("authors", "ELENA MARSH", (), ()),
    ("title", None, (), ()),
)


@pytest.mark.parametrize(
    ("destination", "current", "authors", "expected"), _SUGGESTION_TABLE
)
def test_owner_value_suggestions_follow_the_frozen_rules(
    destination: str,
    current: str | None,
    authors: tuple[str, ...],
    expected: tuple[tuple[str, str], ...],
) -> None:
    assert owner_value_suggestions(destination, current, authors) == tuple(
        OwnerSuggestion(rule, value) for rule, value in expected
    )


_UNICODE_CORPUS = (
    "THE SILENT ORCHARD",
    "the harbour of glass",
    "straße der träume",
    "İSTANBUL GECELERİ",
    "ΟΔΥΣΣΕΙΑ ΚΑΙ ΙΛΙΑΔΑ",
    "КНИГА О ВКУСНОЙ ПИЩЕ",
    "שלום עולם",
    "كتاب الأغاني",
    "東京物語 (Disc 2)",
    "Cafe\u0301 society",
    "a",
    "ALL CAPS - PART 3 OF 4",
    "x - y",
    "III IV",
    "o'neil and don't",
    "ǅEMAL BIJEDIĆ",
)


@pytest.mark.parametrize("current", _UNICODE_CORPUS)
def test_suggestions_are_bounded_admissible_changes_and_title_case_is_idempotent(
    current: str,
) -> None:
    suggestions = owner_value_suggestions("title", current, ("Elena Marsh", "x"))
    assert len(suggestions) <= 4
    assert len({suggestion.value for suggestion in suggestions}) == len(suggestions)
    for suggestion in suggestions:
        assert owner_text(suggestion.value)
        assert suggestion.value != current
        if suggestion.rule == "title_case":
            assert not any(
                again.rule == "title_case"
                for again in owner_value_suggestions("title", suggestion.value, ())
            )


def test_unicode_guards_withhold_a_length_changing_case_map() -> None:
    # Lower-casing U+0130 adds a combining dot, so no title case is offered;
    # title-casing ß would widen it, so it stays lowercase.
    assert owner_value_suggestions("title", "İSTANBUL GECELERİ", ()) == ()
    assert owner_value_suggestions("title", "ßtraße der träume", ()) == (
        OwnerSuggestion("title_case", "ßtraße Der Träume"),
    )
