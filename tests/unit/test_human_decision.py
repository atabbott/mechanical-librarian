from __future__ import annotations

import dataclasses
import json

import pytest

from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import (
    HUMAN_DECISION_SCHEMA,
    HumanDecision,
    HumanDecisionError,
    OwnerSuggestion,
    admitted_applications,
    chosen_value_fingerprint,
    decision_actions,
    genre_subset_available,
    identity_contradicts,
    list_union,
    manual_cover_eligible,
    owner_entry_admitted,
    owner_text,
    owner_value_admitted,
    owner_value_suggestions,
    pinned_field_fingerprint,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.audiobook_reports import admitted_audiobook_report
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


@pytest.mark.parametrize(
    "outcome",
    ("accept", "keep_current", "reject", "owner_entered", "withdraw"),
)
def test_all_five_outcomes_are_canonical_and_exact_version_bound(
    case: ReviewCase, proposal: dict[str, object], outcome: str
) -> None:
    value = {
        "accept": proposal["result"]["alternatives"][0]["value"],
        "owner_entered": {"kind": "text", "value": "The Silent Orchard"},
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
    )
    assert HumanDecision.from_json(decision.to_json()) == decision
    assert json.loads(decision.to_json())["schema_version"] == HUMAN_DECISION_SCHEMA
    decision.validate_for(case)
    decision.validate_new_for(case)


def _owner(
    case: ReviewCase,
    destination: str,
    value: object,
    *,
    kind: str = "text",
    outcome: str = "owner_entered",
    previous: object = None,
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
        previous=previous,
    )


@pytest.mark.parametrize(
    ("value", "code"),
    (
        ("The Silent Orchard", None),
        ("x" * 512, None),
        ("x" * 513, "owner_value_invalid"),
        ("", "owner_value_required"),
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


def test_v3_requires_exactly_its_keys_and_has_no_revert(
    case: ReviewCase, proposal: dict[str, object]
) -> None:
    entered = _owner(case, "title", "An Earlier Title")
    raw = json.loads(entered.to_json())
    assert "reverts_plan_id" not in raw
    for tampered in (
        {**raw, "schema_version": "human-decision-v2"},
        {**raw, "reverts_plan_id": None},
        {**raw, "outcome": "revert"},
        {**raw, "selected_value": None},
    ):
        with pytest.raises(HumanDecisionError):
            HumanDecision.from_json(
                json.dumps(tampered, separators=(",", ":"), sort_keys=True)
            )
    with pytest.raises(HumanDecisionError) as invalid:
        _owner(case, "title", "An Earlier Title", outcome="revert")
    assert invalid.value.code == "invalid"


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
        ("Drama", True, False, False),
        ("Fantasy", True, True, False),
        ("Mystery", False, True, False),
        ("Romance", False, True, False),
    )
    # What the field held before its latest write joins last, tagged only
    # where nothing else lists it.
    assert list_union(proposal, ["Drama", "Fantasy"], ["Mystery", "Horror"]) == (
        ("Drama", True, False, False),
        ("Fantasy", True, True, False),
        ("Mystery", False, True, False),
        ("Romance", False, True, False),
        ("Horror", False, False, True),
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


def test_a_previous_genre_is_admitted_only_with_its_pre_image(
    genre_case: ReviewCase,
) -> None:
    with pytest.raises(HumanDecisionError) as refused:
        _owner(genre_case, "genres", ["Fantasy", "Horror"], kind="list")
    assert refused.value.code == "owner_value_invalid"
    decision = _owner(
        genre_case, "genres", ["Fantasy", "Horror"], kind="list", previous=["Horror"]
    )
    decision.validate_new_for(genre_case, ["Horror"])
    with pytest.raises(HumanDecisionError) as unbound:
        decision.validate_new_for(genre_case)
    assert unbound.value.code == "owner_value_invalid"


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
            # The other fields stay: a choice binds its field's whole offer.
            "proposals": tuple(
                preserved if value["proposal_id"] == preserved["proposal_id"] else value
                for value in case.proposals
            ),
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
            # The other fields stay: a choice binds its field's whole offer.
            "proposals": tuple(
                resolved if value["proposal_id"] == resolved["proposal_id"] else value
                for value in case.proposals
            ),
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


@pytest.mark.parametrize(
    ("identity", "contradicts"),
    [
        ({"outcome": "conflict", "reasons": ["edition_work_contradiction"]}, True),
        (
            {
                "outcome": "unknown",
                "reasons": ["retained_creator_or_contributor_disagreement"],
            },
            True,
        ),
        ({"outcome": "unknown", "reasons": ["work_identity_level_refused"]}, False),
        ({"outcome": "plausible_match", "reasons": []}, False),
        ({"outcome": "verified_work_match", "reasons": []}, False),
    ],
    ids=[
        "conflict",
        "creator_disagreement",
        "level_refused",
        "plausible",
        "verified",
    ],
)
def test_identity_contradiction_is_shared_with_owner_cover_eligibility(
    identity: dict[str, object], contradicts: bool
) -> None:
    """One predicate: the stage holds what owner choice refuses (ADR-0094 S6)."""
    assert identity_contradicts(identity) is contradicts
    candidate = {"incumbent": False, "identity": identity}
    assert manual_cover_eligible(candidate) is not contradicts


# --- pins (ADR-0085, Amendment 2) ---------------------------------------------


def _case_with(*fields: TargetField) -> ReviewCase:
    """A case whose target holds these fields and the default rest."""
    replaced = {field.destination: field for field in fields}
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            tuple(
                replaced.get(field.destination, field)
                for field in default_target_fields("Title")
            ),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    return ReviewCase.create(evaluate_report(report), report)


def _title(state: TargetFieldState, value: str | None = None) -> TargetField:
    return TargetField(TargetDestination.TITLE, state, value)


@pytest.mark.parametrize("outcome", ("pin", "unpin"), ids=("pin", "unpin"))
def test_pin_and_unpin_are_valueless_owner_decisions(outcome: str) -> None:
    case = _case_with()
    proposal = next(
        value for value in case.proposals if value["destination"] == "title"
    )
    assert decision_actions(proposal) == ()
    decision = _create(case, proposal, outcome, None)
    raw = json.loads(decision.to_json())
    assert (raw["schema_version"], raw["outcome"], raw["selected_value"]) == (
        HUMAN_DECISION_SCHEMA,
        outcome,
        None,
    )
    assert HumanDecision.from_json(decision.to_json()) == decision
    decision.validate_for(case)
    decision.validate_new_for(case)


def test_a_cover_cannot_be_pinned(case: ReviewCase) -> None:
    with pytest.raises(HumanDecisionError) as refused:
        _owner(case, "cover", None, outcome="pin")
    assert refused.value.code == "unsupported"


@pytest.mark.parametrize("outcome", ("pin", "unpin"), ids=("pin", "unpin"))
def test_a_pin_with_a_value_is_refused(case: ReviewCase, outcome: str) -> None:
    with pytest.raises(HumanDecisionError) as refused:
        _owner(case, "title", "The Silent Orchard", outcome=outcome)
    assert refused.value.code == "invalid_selection"


def test_an_unreadable_value_cannot_be_pinned() -> None:
    case = _case_with(_title(TargetFieldState.UNLOADED))
    with pytest.raises(HumanDecisionError) as refused:
        _owner(case, "title", None, outcome="pin")
    assert refused.value.code == "unsupported"
    # Nothing stands to release, so an unpin is the store's to refuse.
    _owner(case, "title", None, outcome="unpin")


@pytest.mark.parametrize("version", ("human-decision-v5", "human-decision-v6"))
def test_an_earlier_decision_document_is_refused(
    case: ReviewCase, version: str
) -> None:
    """A v6 decision named `follows` on a keep only, so its null on another
    outcome would read as following no write (#1973)."""
    raw = json.loads(_owner(case, "title", None, outcome="keep_current").to_json())
    with pytest.raises(HumanDecisionError):
        HumanDecision.from_json(
            json.dumps(
                {**raw, "schema_version": version},
                separators=(",", ":"),
                sort_keys=True,
            )
        )


@pytest.mark.parametrize(
    "field",
    (
        _title(TargetFieldState.MISSING),
        _title(TargetFieldState.NULL),
        _title(TargetFieldState.EMPTY),
    ),
    ids=("missing", "null", "empty_state"),
)
def test_pinned_field_fingerprint_treats_missing_null_and_empty_alike(
    field: TargetField,
) -> None:
    empty = pinned_field_fingerprint(
        _case_with(_title(TargetFieldState.MISSING)), "title"
    )
    assert empty is not None
    assert pinned_field_fingerprint(_case_with(field), "title") == empty
    assert pinned_field_fingerprint(_case_with(), "title") != empty


@pytest.mark.parametrize(
    "state",
    (TargetFieldState.UNLOADED, TargetFieldState.MALFORMED, TargetFieldState.OVERSIZED),
    ids=("unloaded", "malformed", "oversized"),
)
def test_pinned_field_fingerprint_is_none_for_unreadable_values(
    state: TargetFieldState,
) -> None:
    assert pinned_field_fingerprint(_case_with(_title(state)), "title") is None


def test_pinned_field_fingerprint_ignores_description_restorable() -> None:
    text = "A synthetic description of a quiet orchard."

    def described(restorable: bool) -> str | None:
        return pinned_field_fingerprint(
            _case_with(
                TargetField(
                    TargetDestination.DESCRIPTION,
                    TargetFieldState.VALUE,
                    text,
                    restorable,
                )
            ),
            "description",
        )

    assert described(True) == described(False) is not None


def test_pinned_field_fingerprint_distinguishes_values_and_destinations() -> None:
    title = pinned_field_fingerprint(_case_with(), "title")
    assert title is not None
    assert pinned_field_fingerprint(_case_with(), "title") == title
    assert (
        pinned_field_fingerprint(
            _case_with(_title(TargetFieldState.VALUE, "Another title")), "title"
        )
        != title
    )
    # Two empty fields are not the same pin.
    assert pinned_field_fingerprint(_case_with(), "subtitle") != (
        pinned_field_fingerprint(_case_with(), "publisher")
    )


# K-D17: a pin on a chosen value keeps the digest a later case showing that
# value has, for each kind of value a field holds.
@pytest.mark.parametrize(
    "destination,selected,held",
    (
        (
            TargetDestination.TITLE,
            {"kind": "text", "value": "The Silent Orchard"},
            "The Silent Orchard",
        ),
        (
            TargetDestination.GENRES,
            {"kind": "list", "value": ["Fantasy", "Drama"]},
            ("Fantasy", "Drama"),
        ),
        (
            TargetDestination.SERIES,
            {
                "kind": "series",
                "value": [
                    {"name": "Example Series", "sequence": "3"},
                    {"name": "Another Series", "sequence": None},
                ],
            },
            (TargetSeries("Example Series", "3"), TargetSeries("Another Series")),
        ),
        (TargetDestination.EXPLICIT, {"kind": "boolean", "value": False}, False),
        (
            TargetDestination.DESCRIPTION,
            {"kind": "text", "value": "A synthetic description of a quiet orchard."},
            "A synthetic description of a quiet orchard.",
        ),
    ),
    ids=("text", "list", "series", "boolean", "description"),
)
def test_chosen_value_fingerprint_matches_the_value_read_back(
    destination: TargetDestination, selected: dict[str, object], held: object
) -> None:
    case = _case_with(TargetField(destination, TargetFieldState.VALUE, held))
    chosen = chosen_value_fingerprint(destination.value, selected)
    assert chosen == pinned_field_fingerprint(case, destination.value)
    assert chosen != pinned_field_fingerprint(_case_with(), destination.value)


def _description_proposal(
    *assessments: str, destination: str = "description", outcome: str = "keep"
) -> dict[str, object]:
    """A proposal whose alternatives carry these assessments, in order."""
    return {
        "destination": destination,
        "manual_required": False,
        "result": {
            "outcome": outcome,
            "alternatives": [
                {
                    "assessment": assessment,
                    "value": {"kind": "text", "value": f"Source {index}."},
                }
                for index, assessment in enumerate(assessments)
            ],
        },
    }


def test_keep_current_is_admitted_on_a_description_every_source_repeats() -> None:
    """Keep current stands beside description advice there (#1905)."""
    from mechanical_librarian.domain.human_decision import (
        preserved_description,
        validate_decision_action,
    )

    repeated = _description_proposal("preserves_target", "preserves_target")
    assert preserved_description(repeated)
    # The domain's own choices stay empty: the offer is advice's to make.
    assert decision_actions(repeated) == ()
    validate_decision_action(repeated, "keep_current", None)
    with pytest.raises(HumanDecisionError) as selected:
        validate_decision_action(
            repeated, "keep_current", {"kind": "text", "value": "Source 0."}
        )
    assert selected.value.code == "invalid_selection"
    with pytest.raises(HumanDecisionError) as accepted:
        validate_decision_action(
            repeated, "accept", {"kind": "text", "value": "Source 0."}
        )
    assert accepted.value.code == "unsupported"
    for negative in (
        _description_proposal("preserves_target", destination="title"),
        _description_proposal(),
        _description_proposal("preserves_target", "replaces_target"),
    ):
        assert not preserved_description(negative)
    with pytest.raises(HumanDecisionError) as other_field:
        validate_decision_action(
            _description_proposal("preserves_target", destination="title"),
            "keep_current",
            None,
        )
    assert other_field.value.code == "unsupported"
    with pytest.raises(HumanDecisionError) as empty:
        validate_decision_action(_description_proposal(), "keep_current", None)
    assert empty.value.code == "unsupported"


# --- going back, clearing and names (#1523, ADR-0105) --------------------------

_EMPTY = {"kind": "empty", "value": None}


def _audiobook(narrators: tuple[str, ...] | None = None) -> ReviewCase:
    report = admitted_audiobook_report()
    if narrators is not None:
        target = report.target
        report = MetadataReport.create(
            target=TargetFacts(
                target.connection,
                target.library,
                target.item,
                tuple(
                    TargetField(field.destination, TargetFieldState.VALUE, narrators)
                    if field.destination is TargetDestination.NARRATORS
                    else field
                    for field in target.fields
                ),
            ),
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            audio=report.audio,
        )
    return ReviewCase.create(evaluate_report(report), report)


_AUTHORS = TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada",))


def _entered(
    case: ReviewCase, destination: str, selected_value: dict[str, object] | None
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
        outcome="owner_entered",
        selected_value=selected_value,
        idempotency_key="domain-owner-value-0001",
        occurred_at=1_000,
    )


def _refusal(
    case: ReviewCase, destination: str, value: dict[str, object] | None
) -> str:
    with pytest.raises(HumanDecisionError) as refused:
        _entered(case, destination, value)
    return refused.value.code


@pytest.mark.parametrize(
    ("destination", "held"),
    (
        (TargetDestination.SUBTITLE, "An Old Subtitle"),
        (TargetDestination.PUBLISHER, "Harbour Press"),
        (TargetDestination.GENRES, ("Drama",)),
    ),
    ids=("subtitle", "publisher", "genres"),
)
def test_a_clear_is_the_closed_empty_kind_on_a_clearable_field(
    destination: TargetDestination, held: object
) -> None:
    case = _case_with(TargetField(destination, TargetFieldState.VALUE, held))
    decision = _entered(case, destination.value, dict(_EMPTY))
    assert decision.selected_value == _EMPTY
    assert HumanDecision.from_json(decision.to_json()) == decision
    decision.validate_for(case)
    decision.validate_new_for(case)
    # The same intent in another spelling is refused, never rewritten.
    kind = "list" if destination is TargetDestination.GENRES else "text"
    empty = [] if kind == "list" else ""
    assert (
        _refusal(case, destination.value, {"kind": kind, "value": empty})
        == "owner_value_invalid"
    )


def test_narrators_clear_on_an_audiobook_and_an_empty_field_is_unchanged() -> None:
    held = _audiobook(("Old Reader",))
    assert _entered(held, "narrators", dict(_EMPTY)).selected_value == _EMPTY
    # A field that reads empty refuses a clear as unchanged.
    assert _refusal(_audiobook(), "narrators", dict(_EMPTY)) == "owner_value_unchanged"
    genres = ReviewCase.create(evaluate_report(genre_report()), genre_report())
    assert _refusal(genres, "genres", dict(_EMPTY)) == "owner_value_unchanged"


@pytest.mark.parametrize(
    ("destination", "value"),
    (
        ("title", _EMPTY),
        ("title", {"kind": "text", "value": ""}),
        ("authors", _EMPTY),
        ("authors", {"kind": "list", "value": []}),
    ),
    ids=(
        "title-empty-kind",
        "title-empty-text",
        "authors-empty-kind",
        "authors-no-name",
    ),
)
def test_title_and_authors_refuse_an_empty_value_as_required(
    destination: str, value: dict[str, object]
) -> None:
    case = _case_with(_AUTHORS)
    assert _refusal(case, destination, dict(value)) == "owner_value_required"


def test_refusals_follow_their_fixed_order() -> None:
    case = _case_with(_AUTHORS)
    # Unsupported outranks required: ISBN is never entered.
    assert _refusal(case, "isbn", dict(_EMPTY)) == "unsupported"
    # A non-clearable field outside owner entry is unsupported before invalid.
    assert _refusal(case, "series", dict(_EMPTY)) == "unsupported"
    # Required outranks invalid: an empty authors list is the same intent.
    assert (
        _refusal(case, "authors", {"kind": "list", "value": []})
        == "owner_value_required"
    )
    # An ineligible field takes no owner value at all.
    assert _refusal(case, "subtitle", dict(_EMPTY)) == "unsupported"


@pytest.mark.parametrize(
    "outcome",
    ("accept", "keep_current", "withdraw"),
    ids=("accept", "keep", "withdraw"),
)
def test_only_an_owner_entered_decision_carries_the_empty_kind(
    case: ReviewCase, proposal: dict[str, object], outcome: str
) -> None:
    with pytest.raises(HumanDecisionError) as refused:
        _create(case, proposal, outcome, dict(_EMPTY))
    assert refused.value.code == "invalid_selection"
    raw = json.loads(_owner(case, "title", None, outcome="keep_current").to_json())
    for tampered in (
        {**raw, "selected_value": dict(_EMPTY)},
        {**raw, "outcome": "accept", "selected_value": dict(_EMPTY)},
    ):
        with pytest.raises(HumanDecisionError):
            HumanDecision.from_json(
                json.dumps(tampered, separators=(",", ":"), sort_keys=True)
            )
    entered = json.loads(
        _entered(
            _case_with(
                TargetField(TargetDestination.SUBTITLE, TargetFieldState.VALUE, "Sub")
            ),
            "subtitle",
            dict(_EMPTY),
        ).to_json()
    )
    with pytest.raises(HumanDecisionError):
        HumanDecision.from_json(
            json.dumps(
                {**entered, "selected_value": {"kind": "empty", "value": "x"}},
                separators=(",", ":"),
                sort_keys=True,
            )
        )


@pytest.mark.parametrize(
    ("value", "code"),
    (
        (["Ada Example"], None),
        (["Ada Example", "Bea Example"], None),
        (["Bea Example", "Ada Example"], None),
        ([f"Name {index}" for index in range(32)], None),
        ([f"Name {index}" for index in range(33)], "owner_value_invalid"),
        (["Ada Example", "Ada Example"], "owner_value_invalid"),
        # Names that differ only by case are one name (#1523).
        (["Ada Example", "ada EXAMPLE"], "owner_value_invalid"),
        (["Anna Straße", "Anna Strasse"], "owner_value_invalid"),
        (["x" * 513], "owner_value_invalid"),
        (["Ada\u0007Example"], "owner_value_invalid"),
        (["/srv/library/book.epub"], "owner_value_invalid"),
        (["Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD"], "owner_value_invalid"),
        (["https://example.invalid/author"], "owner_value_invalid"),
        ([" Ada Example"], "owner_value_invalid"),
        (["Corin Halloway"], "owner_value_unchanged"),
    ),
    ids=(
        "one",
        "two",
        "owner-order",
        "thirty-two",
        "thirty-three",
        "duplicate",
        "duplicate-by-case",
        "duplicate-by-folding",
        "over-long",
        "control-character",
        "path",
        "credential",
        "uri",
        "not-canonical",
        "unchanged",
    ),
)
@pytest.mark.parametrize(
    "destination", ("authors", "narrators"), ids=("authors", "narrators")
)
def test_names_are_one_to_thirty_two_owner_texts_in_the_owner_order(
    destination: str, value: list[str], code: str | None
) -> None:
    case = _audiobook(("Corin Halloway",))
    selected = {"kind": "list", "value": value}
    if code is None:
        assert _entered(case, destination, selected).selected_value == selected
        return
    assert _refusal(case, destination, selected) == code


def test_narrators_take_no_owner_entry_on_an_ebook() -> None:
    case = _case_with(
        _AUTHORS,
        TargetField(TargetDestination.NARRATORS, TargetFieldState.VALUE, ("Reader",)),
    )
    narrators = next(
        value for value in case.proposals if value["destination"] == "narrators"
    )
    assert owner_value_admitted(narrators)
    assert not owner_entry_admitted(narrators, case.media_kind)
    for value in (_EMPTY, {"kind": "list", "value": ["Another Reader"]}):
        assert _refusal(case, "narrators", dict(value)) == "unsupported"
    # Authors take names on both media.
    assert _entered(
        case, "authors", {"kind": "list", "value": ["Ada Example"]}
    ).selected_value == {"kind": "list", "value": ["Ada Example"]}


def test_a_single_current_genre_is_offered_so_it_can_be_cleared() -> None:
    case = _case_with(
        TargetField(TargetDestination.GENRES, TargetFieldState.VALUE, ("Drama",))
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "genres"
    )
    assert genre_subset_available(proposal, ["Drama"])
    assert not genre_subset_available(proposal, None)


def test_a_chosen_clear_is_pinned_as_the_empty_state() -> None:
    chosen = chosen_value_fingerprint("subtitle", dict(_EMPTY))
    for state in (
        TargetFieldState.MISSING,
        TargetFieldState.NULL,
        TargetFieldState.EMPTY,
    ):
        empty = _case_with(TargetField(TargetDestination.SUBTITLE, state, None))
        assert chosen == pinned_field_fingerprint(empty, "subtitle")
    held = _case_with(
        TargetField(TargetDestination.SUBTITLE, TargetFieldState.VALUE, "Sub")
    )
    assert chosen != pinned_field_fingerprint(held, "subtitle")
    # A field that cannot be emptied has no empty choice to pin.
    with pytest.raises(FieldEffectError):
        chosen_value_fingerprint("title", dict(_EMPTY))


def test_every_decision_names_the_write_it_follows(case: ReviewCase) -> None:
    """`follows` is frozen by the store when a decision is recorded and is no
    part of the request: it round-trips on any outcome, leaves the decision's
    identity and request digest alone, and is refused malformed (#1523,
    #1973)."""
    keep = _owner(case, "title", None, outcome="keep_current")
    written = "human_decision_" + "a" * 64
    frozen = dataclasses.replace(keep, follows=written)
    assert HumanDecision.from_json(frozen.to_json()) == frozen
    assert (frozen.decision_id, frozen.request_digest) == (
        keep.decision_id,
        keep.request_digest,
    )
    raw = json.loads(keep.to_json())
    assert raw["follows"] is None
    other = _owner(case, "title", "Another Title")
    followed = dataclasses.replace(other, follows=written)
    assert HumanDecision.from_json(followed.to_json()) == followed
    assert json.loads(followed.to_json())["follows"] == written
    for document in (
        {**raw, "follows": "effect_" + "a" * 64},
        {**raw, "follows": ""},
        {**raw, "follows": 1},
        {key: value for key, value in raw.items() if key != "follows"},
    ):
        with pytest.raises(HumanDecisionError):
            HumanDecision.from_json(
                json.dumps(document, separators=(",", ":"), sort_keys=True)
            )
