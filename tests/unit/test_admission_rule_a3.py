"""The A3 bibliographic-combination analysis of the admission-rule ledger (#1746).

Synthetic only: combinations are built here as the child would report them,
and every free-text slot carries a sentinel, so a leaked value shows up in the
output. One test replays a product-built database through the real child.
"""

from __future__ import annotations

import dataclasses
import io
import json
import shutil
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest
import scripts.admission_rule_a3 as a3
import scripts.admission_rule_ledger as ledger

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.unit.test_admission_rule_a6 import _breakdown_run
from tests.unit.test_admission_rule_ledger import (
    _SENTINEL,
    EBOOK_REVISION,
    REPOSITORY,
    Fixture,
    Sentinels,
    _arguments,
    _database,
    _run,
    _strings,
    _test_vocabulary,
)
from tests.unit.test_bibliographic_bundles import _bibliographic_report
from tests.unit.test_bibliographic_bundles import _source as _bibliographic_source

POLICY = "fixed-conservative-v12"
SCALAR = ("authors", "language", "narrators", "publishedDate", "publisher", "title")
P, C = a3.PRESERVE, a3.CANDIDATE


def _part(
    kind: str = P,
    alternative: str | None = None,
    *,
    state: str = "missing",
    texts: Sequence[tuple[str, ...]] = (),
    items: Sequence[a3.SeriesItem] = (),
) -> a3.Component:
    return a3.Component(kind, alternative, state, tuple(texts), tuple(items))


def _series(
    name: tuple[str, ...], sequence: tuple[str, ...] | None = None
) -> a3.SeriesItem:
    return a3.SeriesItem(
        name, sequence, None if sequence is None else (*name, *sequence)
    )


def _combination(
    title: tuple[str, ...] | None,
    *,
    kind: str = P,
    alternative: str | None = None,
    kept: bool = True,
    subtitle: a3.Component | None = None,
    series: a3.Component | None = None,
    value: str | None = None,
    verdict: bool | None = None,
) -> a3.Combination:
    built = a3.Combination(
        title_kind=kind,
        title_alternative=alternative,
        title_tokens=title,
        title_value=value,
        title_kept=kept,
        subtitle=subtitle or _part(),
        series=series or _part(),
        verdict=False,
    )
    if verdict is None:
        verdict = bool(a3.clauses(built, same_name_rule=False))
    return dataclasses.replace(built, verdict=verdict)


def _prior(
    outcome: str,
    *,
    selected: str | None = None,
    assessment: str | None = None,
    alternatives: int = 1,
    all_preserve: bool = False,
    reasons: Sequence[str] = (),
) -> a3.Prior:
    return a3.Prior(
        outcome, tuple(reasons), selected, assessment, alternatives, all_preserve
    )


def _replay(
    combinations: Sequence[a3.Combination],
    prior: dict[str, a3.Prior],
    a3_fields: Sequence[str] = ("title", "series"),
) -> a3.CaseReplay:
    return a3.CaseReplay("equal", tuple(a3_fields), tuple(combinations), "equal", prior)


def _case(
    results: Mapping[str, Mapping[str, object]] | None = None,
    labels: dict[str, str] | None = None,
) -> ledger.Case:
    labels = labels or {}
    return ledger.Case(
        medium="ebook",
        epub=False,
        report={},
        results={key: dict(value) for key, value in (results or {}).items()},
        shadow={},
        cover_candidates=[],
        cover_omissions=set(),
        records={record: (0, "work", False) for record in labels},
        labels=dict(labels),
    )


# The R1 shape: a kept two-word title that already holds the one-word series
# name, and a series fill with one alternative.
TITLE = ("ALPHA", "BETA")
NAME = ("ALPHA",)


def _r1_combination(**overrides: object) -> a3.Combination:
    base: dict[str, object] = {
        "series": _part(C, "fill", items=[_series(NAME)]),
    }
    return _combination(TITLE, **{**base, **overrides})  # type: ignore[arg-type]


def _r1_prior(**series: object) -> dict[str, a3.Prior]:
    fill: dict[str, object] = {"selected": "fill", "assessment": "fills_empty"}
    return {
        "title": _prior("keep", selected="kept", assessment="preserves_target"),
        "series": _prior("propose_change", **{**fill, **series}),  # type: ignore[arg-type]
        "subtitle": _prior("keep"),
    }


# --- The mirrored duplicate check ------------------------------------------


@pytest.mark.parametrize(
    ("combination", "expected"),
    [
        (
            _combination(TITLE, kind=C, subtitle=_part(C, "s", texts=[("BETA",)])),
            ("subtitle_in_title",),
        ),
        (
            _combination(NAME, kind=C, series=_part(C, "x", items=[_series(NAME)])),
            ("series_name_equals_title",),
        ),
        (_r1_combination(), ("series_name_in_title",)),
        (
            _combination(
                ("GAMMA", "3"), series=_part(C, "x", items=[_series(NAME, ("3",))])
            ),
            ("series_number_in_title",),
        ),
        (
            _combination(
                ("ALPHA", "3", "BETA"),
                kind=C,
                subtitle=_part(C, "s", texts=[("BETA",)]),
                series=_part(C, "x", items=[_series(NAME, ("3",))]),
            ),
            ("subtitle_in_title", "series_name_in_title", "series_number_in_title"),
        ),
        (
            _combination(TITLE, series=_part(P, items=[_series(NAME)])),
            (),
        ),
        (_combination(None, kind=C, series=_part(C, "x", items=[_series(NAME)])), ()),
    ],
    ids=[
        "subtitle",
        "name-equals-new-title",
        "name-inside",
        "number",
        "every-clause-in-order",
        "both-kept-is-never-checked",
        "no-title-text",
    ],
)
def test_clauses_name_every_firing_clause_in_order(
    combination: a3.Combination, expected: tuple[str, ...]
) -> None:
    assert a3.clauses(combination, same_name_rule=False) == expected


def _same_name(**overrides: object) -> a3.Combination:
    base: dict[str, object] = {"series": _part(C, "x", items=[_series(TITLE)])}
    return _combination(TITLE, **{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("combination", "allowed"),
    [
        (_same_name(), True),
        (_same_name(series=_part(C, "x", state="empty", items=[_series(TITLE)])), True),
        (
            _same_name(series=_part(C, "x", state="value", items=[_series(TITLE)])),
            False,
        ),
        (_same_name(kind=C, alternative="new"), False),
        (_same_name(kept=False), False),
        (_r1_combination(), False),
        (
            _same_name(series=_part(C, "x", items=[_series(TITLE, ("2",))])),
            True,
        ),
    ],
    ids=[
        "kept-title-missing-series",
        "kept-title-empty-series",
        "populated-series",
        "new-title",
        "title-not-kept",
        "name-inside-not-equal",
        "number-not-in-title",
    ],
)
def test_main_same_name_rule_allows_only_its_case(
    combination: a3.Combination, allowed: bool
) -> None:
    # Without the rule (the ebook source) every one of these is vetoed.
    assert a3.clauses(combination, same_name_rule=False)
    assert (not a3.clauses(combination, same_name_rule=True)) is allowed
    replay = _replay([combination], {})
    assert a3.stale_for_main(replay) is allowed


def test_a_number_in_the_title_still_vetoes_a_same_name_series() -> None:
    combination = _combination(
        ("ALPHA", "2"), series=_part(C, "x", items=[_series(("ALPHA", "2"), ("2",))])
    )
    assert a3.clauses(combination, same_name_rule=True) == ("series_number_in_title",)


# --- Cause per work ---------------------------------------------------------


def test_each_work_counts_under_the_first_clause_of_each_combination() -> None:
    replay = _replay(
        [
            _combination(
                ("ALPHA", "3", "BETA"),
                subtitle=_part(C, "s", texts=[("BETA",)]),
                series=_part(C, "x", items=[_series(NAME, ("3",))]),
            ),
            _r1_combination(),
            _r1_combination(subtitle=_part(C, "t", texts=[("DELTA",)])),
        ],
        {},
    )
    assert a3.work_causes(replay, 256, same_name_rule=False) == (
        "subtitle_in_title",
        "series_name_in_title",
    )


def test_ambiguity_and_the_cap_are_work_level_causes() -> None:
    assert a3.work_causes(_replay([], {}), 256, same_name_rule=False) == (
        "ambiguous_source",
    )
    survivors = [
        _combination(TITLE, series=_part(C, str(index)), verdict=False)
        for index in range(3)
    ]
    assert a3.work_causes(_replay(survivors, {}), 2, same_name_rule=False) == (
        "too_many_combinations",
    )
    # Under the cap a survivor would have formed a combination: not explained.
    assert a3.work_causes(_replay(survivors, {}), 3, same_name_rule=False) == (
        "not_explained",
    )


# --- Classes from the outcome before the overwrite -------------------------


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("propose_change", "value_change"),
        ("acceptable_choice", "value_change"),
        ("keep", "kept_value"),
        ("unresolved_choice", "held_first"),
        ("defer", "held_first"),
        (None, "held_first"),
    ],
    ids=["change", "acceptable", "keep", "competing", "defer", "missing"],
)
def test_a_field_is_classed_by_its_outcome_before_the_overwrite(
    outcome: str | None, expected: str
) -> None:
    assert a3.classify(None if outcome is None else _prior(outcome)) == expected


# --- R1 ---------------------------------------------------------------------


def test_r1_admits_the_series_fill_beside_a_kept_title_holding_the_name() -> None:
    combination = _r1_combination()
    verdict = a3.relax_r1(_replay([combination], _r1_prior()), _case())
    assert verdict == a3.Verdict("admitted", ("series",), combination)


@pytest.mark.parametrize(
    ("combinations", "prior", "outcome"),
    [
        (
            [
                _r1_combination(),
                _r1_combination(subtitle=_part(C, "t", texts=[("Z",)])),
            ],
            _r1_prior(),
            "refused_ambiguous",
        ),
        (
            [_r1_combination(subtitle=_part(C, "t", texts=[("Z",)]))],
            {**_r1_prior(), "subtitle": _prior("unresolved_choice")},
            "refused_masked",
        ),
        (
            [_r1_combination()],
            {**_r1_prior(), "title": _prior("unresolved_choice")},
            "refused_masked",
        ),
        (
            [
                _combination(
                    ("ALPHA", "3"),
                    series=_part(C, "fill", items=[_series(NAME, ("3",))]),
                )
            ],
            _r1_prior(),
            "refused_clause",
        ),
        (
            [_r1_combination(subtitle=_part(C, "t", texts=[("BETA",)]))],
            _r1_prior(),
            "refused_clause",
        ),
        (
            [
                _r1_combination(
                    series=_part(C, "fill", state="value", items=[_series(NAME)])
                )
            ],
            _r1_prior(),
            "refused_populated",
        ),
        (
            [_r1_combination(value="ALPHA BETA: Now a Major Motion Picture")],
            _r1_prior(),
            "refused_degraded",
        ),
        ([_r1_combination()], _r1_prior(alternatives=2), "refused_shape"),
        ([_r1_combination()], _r1_prior(assessment="replaces_target"), "refused_shape"),
        ([_r1_combination()], _r1_prior(selected="other"), "refused_shape"),
        (
            [_r1_combination()],
            {**_r1_prior(), "title": _prior("propose_change")},
            "refused_shape",
        ),
        (
            [_combination(TITLE, series=_part(C, "fill", items=[_series(TITLE)]))],
            _r1_prior(),
            "main_forms",
        ),
    ],
    ids=[
        "several-combinations",
        "held-first-member",
        "held-first-title",
        "number-hit",
        "subtitle-hit",
        "populated-series",
        "degraded-title",
        "two-alternatives",
        "not-a-fill",
        "another-selection",
        "new-title",
        "main-forms-one",
    ],
)
def test_r1_refuses_each_listed_condition(
    combinations: list[a3.Combination], prior: dict[str, a3.Prior], outcome: str
) -> None:
    verdict = a3.relax_r1(_replay(combinations, prior), _case())
    assert (verdict.outcome, verdict.admitted) == (outcome, ())


def test_r1_refuses_a_fill_that_cites_a_wrong_work_record() -> None:
    results = {
        "series": {
            "alternatives": [{"alternative_id": "fill", "evidence_references": ["r"]}]
        }
    }
    replay = _replay([_r1_combination()], _r1_prior())
    assert a3.relax_r1(replay, _case(results, {"r": "negative"})).outcome == (
        "refused_negative"
    )
    assert a3.relax_r1(replay, _case(results, {"r": "positive"})).outcome == "admitted"


# --- R2 ---------------------------------------------------------------------


def _r2_combination(**overrides: object) -> a3.Combination:
    base: dict[str, object] = {
        "kind": C,
        "alternative": "new",
        "series": _part(C, "fill", items=[_series(TITLE)]),
    }
    return _combination(TITLE, **{**base, **overrides})  # type: ignore[arg-type]


def _r2_prior(**title: object) -> dict[str, a3.Prior]:
    base: dict[str, object] = {"selected": "new", "assessment": "replaces_target"}
    return {
        "title": _prior("propose_change", **{**base, **title}),  # type: ignore[arg-type]
        "series": _prior("propose_change", selected="fill", assessment="fills_empty"),
        "subtitle": _prior("keep"),
    }


def test_r2_admits_a_new_title_and_its_same_name_series() -> None:
    combination = _r2_combination()
    verdict = a3.relax_r2(_replay([combination], _r2_prior()), _case())
    assert verdict == a3.Verdict("admitted", ("title", "series"), combination)


@pytest.mark.parametrize(
    ("combinations", "prior", "outcome"),
    [
        (
            [
                _r2_combination(
                    series=_part(C, "fill", state="value", items=[_series(TITLE)])
                )
            ],
            _r2_prior(),
            "refused_populated",
        ),
        (
            [_r2_combination(series=_part(C, "fill", items=[_series(NAME)]))],
            _r2_prior(),
            "refused_clause",
        ),
        (
            [
                _r2_combination(),
                _r2_combination(subtitle=_part(C, "t", texts=[("Z",)])),
            ],
            _r2_prior(),
            "refused_ambiguous",
        ),
        (
            [_r2_combination(subtitle=_part(C, "t", texts=[("Z",)]))],
            {**_r2_prior(), "subtitle": _prior("defer")},
            "refused_masked",
        ),
        (
            [_r2_combination()],
            {**_r2_prior(), "series": _prior("unresolved_choice")},
            "refused_masked",
        ),
        (
            [_r2_combination(value="ALPHA BETA: The International Bestseller")],
            _r2_prior(),
            "refused_degraded",
        ),
        ([_r2_combination()], _r2_prior(selected="other"), "refused_shape"),
        (
            [_r2_combination()],
            {**_r2_prior(), "title": _prior("keep", selected="new")},
            "refused_shape",
        ),
    ],
    ids=[
        "populated-series",
        "name-inside-not-equal",
        "several-combinations",
        "held-first-member",
        "held-first-series",
        "degraded-title",
        "another-title",
        "kept-title",
    ],
)
def test_r2_refuses_each_listed_condition(
    combinations: list[a3.Combination], prior: dict[str, a3.Prior], outcome: str
) -> None:
    verdict = a3.relax_r2(_replay(combinations, prior), _case())
    assert (verdict.outcome, verdict.admitted) == (outcome, ())


def test_r2_number_hit_beside_the_same_name_is_another_clause() -> None:
    combination = _r2_combination(
        series=_part(C, "fill", items=[_series(("ALPHA", "BETA", "2"), ("2",))])
    )
    two = _combination(
        ("ALPHA", "BETA", "2"),
        kind=C,
        alternative="new",
        series=combination.series,
    )
    assert a3.relax_r2(_replay([two], _r2_prior()), _case()).outcome == (
        "refused_clause"
    )
    # A series number the title does not hold changes nothing.
    numbered = _r2_combination(series=_part(C, "fill", items=[_series(TITLE, ("2",))]))
    assert a3.relax_r2(_replay([numbered], _r2_prior()), _case()).outcome == (
        "admitted"
    )


# --- R3 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prior", "outcome"),
    [
        (_prior("keep", all_preserve=True), "admitted"),
        (_prior("keep", all_preserve=False), "refused_shape"),
        (_prior("propose_change", all_preserve=True), "refused_shape"),
        (None, "refused_shape"),
    ],
    ids=["kept-and-preserved", "another-alternative", "a-change", "no-field"],
)
def test_r3_admits_no_value_and_only_a_kept_preserved_field(
    prior: a3.Prior | None, outcome: str
) -> None:
    replay = _replay([_r1_combination()], {} if prior is None else {"title": prior})
    verdict = a3.relax_r3(replay, "title")
    assert (verdict.outcome, verdict.admitted, verdict.combination) == (
        outcome,
        (),
        None,
    )


# --- Screens ----------------------------------------------------------------


def test_the_screen_counts_records_classes_and_repeated_values() -> None:
    results = {
        "series": {
            "alternatives": [
                {
                    "alternative_id": "fill",
                    "assessment": "preserves_target",
                    "evidence_references": ["right", "wrong", "open"],
                },
                {"alternative_id": "other", "evidence_references": ["wrong"]},
            ]
        }
    }
    case = _case(results, {"right": "positive", "wrong": "negative", "open": "x"})
    tally = a3.RelaxationTally()
    a3._screen(case, "series", "fill", tally)
    assert (tally.true_positive, tally.false_positive, tally.unlabelled) == (1, 1, 1)
    assert (tally.fields, tally.unreached, tally.preserves_current) == (1, 0, 1)
    # Neither alternative is right work only: the field has none.
    assert dict(tally.classes) == {"no_right_work": 1}
    nothing = a3.RelaxationTally()
    a3._screen(case, "series", "missing", nothing)
    assert (nothing.unreached, nothing.preserves_current) == (1, 0)


@pytest.mark.parametrize(
    ("labels", "identity", "expected"),
    [
        ({"a": "positive", "b": "positive"}, "a", ("tied", False)),
        ({"a": "positive", "b": "negative"}, "a", ("discriminable", True)),
        ({"a": "negative", "b": "positive"}, "a", ("discriminable", False)),
        ({"a": "negative", "b": "unlabelled"}, "a", ("no_right_work", False)),
    ],
    ids=["tied", "admitted-is-right", "admitted-is-wrong", "none"],
)
def test_field_class_reads_ao_classes_and_the_right_pick(
    labels: dict[str, str], identity: str, expected: tuple[str, bool]
) -> None:
    results = {
        "title": {
            "alternatives": [
                {"alternative_id": name, "evidence_references": [name]}
                for name in labels
            ]
        }
    }
    assert a3.field_class(_case(results, labels), "title", identity) == expected


def _agreement_case(text: Sentinels) -> tuple[ledger.Case, str, str]:
    fixture = Fixture(text, "v26-like")
    first, second = fixture.provider(), fixture.provider()
    work = text()
    cited, cited_claim = fixture.record(first, work)
    other, _claim = fixture.record(second, work)
    fixture.bind(first, cited, work, "accepted")
    fixture.bind(second, other, work, "accepted")
    case = ledger._build_case(
        {"results": fixture.results}, fixture.report, {"outcomes": []}, {}
    )
    providers = case.providers()
    other_value = cast(
        "str",
        ledger._dicts(ledger._dicts(providers[second]["candidates"])[0]["claims"])[0][
            "value"
        ],
    )
    return case, cited_claim, other_value


def test_value_agreement_never_counts_a_record_the_field_cites() -> None:
    text = Sentinels()
    case, cited_claim, other_value = _agreement_case(text)
    agreeing = {
        "value": {"kind": "text", "value": other_value.lower()},
        "evidence_references": [cited_claim],
    }
    assert a3.value_agreement(case, "title", agreeing) is True
    own = {
        "value": {"kind": "text", "value": text()},
        "evidence_references": [cited_claim],
    }
    assert a3.value_agreement(case, "title", own) is False
    # A record the field cites is never a comparison.
    everything = dict(agreeing, evidence_references=list(case.records))
    assert a3.value_agreement(case, "title", everything) is None
    assert a3.value_agreement(case, "series", agreeing) is None


def _claim(kind: str, value: str | None, state: str = "valid") -> dict[str, object]:
    return {
        "reference": kind,
        "kind": kind,
        "value": value,
        "state": state,
        "scope": "work",
    }


def _series_agreement(claims: list[dict[str, object]]) -> tuple[ledger.Case, str]:
    """An uncited, identifier-accepted record stating `claims`."""
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    first, second = fixture.provider(), fixture.provider()
    work = text()
    cited, cited_claim = fixture.record(first, work)
    other, _claim_reference = fixture.record(second, work)
    fixture.bind(first, cited, work, "accepted")
    fixture.bind(second, other, work, "accepted")
    candidates = cast(
        "list[dict[str, object]]", fixture._providers()[second]["candidates"]
    )
    candidates[0]["claims"] = claims
    case = ledger._build_case(
        {"results": fixture.results}, fixture.report, {"outcomes": []}, {}
    )
    return case, cited_claim


def _admitted_series(cited: str, *items: tuple[str, str | None]) -> dict[str, object]:
    return {
        "value": {
            "kind": "series",
            "value": [{"name": name, "sequence": sequence} for name, sequence in items],
        },
        "evidence_references": [cited],
    }


@pytest.mark.parametrize(
    ("claims", "admitted", "expected"),
    [
        (
            [_claim("series", "Alpha"), _claim("series_sequence", "2")],
            ("alpha", "2"),
            True,
        ),
        (
            [_claim("series", "Alpha"), _claim("series_sequence", "2")],
            ("Alpha", "1"),
            False,
        ),
        (
            [_claim("series_sequence", "2"), _claim("series", "Alpha")],
            ("Alpha", "2"),
            True,
        ),
        (
            [_claim("series", "Alpha"), _claim("series_sequence", None, "absent")],
            ("Alpha", None),
            True,
        ),
        ([_claim("series", "Alpha")], ("Alpha", "1"), False),
        (
            [_claim("series", "Alpha", "malformed"), _claim("series_sequence", "2")],
            ("Alpha", "2"),
            None,
        ),
        ([_claim("series", "Alpha"), _claim("series", "Beta")], ("Alpha", None), None),
    ],
    ids=[
        "same-name-and-sequence",
        "same-name-other-sequence",
        "sequence-before-name",
        "absent-sequence",
        "record-without-sequence",
        "invalid-name-states-nothing",
        "two-names-without-sequences",
    ],
)
def test_series_agreement_compares_each_name_with_its_sequence(
    claims: list[dict[str, object]],
    admitted: tuple[str, str | None],
    expected: bool | None,
) -> None:
    case, cited = _series_agreement(claims)
    assert (
        a3.value_agreement(case, "series", _admitted_series(cited, admitted))
        is expected
    )


def test_series_agreement_needs_every_admitted_series() -> None:
    claims = [_claim("series", "Alpha"), _claim("series_sequence", "2")]
    case, cited = _series_agreement(claims)
    both = _admitted_series(cited, ("Alpha", "2"), ("Beta", "1"))
    assert a3.value_agreement(case, "series", both) is False


def test_exact_field_decoding_rejects_subclasses() -> None:
    class Text(str):
        pass

    assert ledger._exact(str, "x") == "x"
    assert ledger._exact(int, 3) == 3
    assert ledger._exact(bool, False) is False
    for kind, value in [(int, True), (str, Text("x")), (bool, 1), (int, "3")]:
        with pytest.raises(ValueError):
            ledger._exact(kind, value)


# --- The analysis through the ledger ---------------------------------------


def _a3_fixture(text: Sentinels) -> tuple[Fixture, dict[str, str]]:
    """One R1-shaped A3 work with a right-work fill, and a masked title."""
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, claim = fixture.record(provider, work)
    fixture.bind(provider, accepted, work, "accepted")
    reason = [a3.A3_REASON]
    fixture.result("title", "unresolved_choice", reason, [claim])
    fixture.result("series", "unresolved_choice", reason, [claim])
    fixture.result("subtitle", "keep", ["no_supported_evidence"])
    fixture.shadow.append(
        {
            "destination": "title",
            "disposition": "conflict",
            "reason_code": a3.ATOMICITY_REASON,
        }
    )
    title, series = fixture.results[0], fixture.results[1]
    return fixture, {
        "title": cast("str", ledger._dicts(title["alternatives"])[0]["alternative_id"]),
        "series": cast(
            "str", ledger._dicts(series["alternatives"])[0]["alternative_id"]
        ),
    }


def _masked_fixture(text: Sentinels) -> Fixture:
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    _record, claim = fixture.record(provider, text())
    fixture.result("title", "unresolved_choice", [a3.A3_REASON], [claim])
    return fixture


def _case_ids(database: Path) -> list[str]:
    connection = ledger._open_snapshot(database)
    try:
        return [
            cast("str", row[0])
            for row in connection.execute(
                "SELECT review_case_id FROM current_review_packages "
                "ORDER BY review_case_id"
            )
        ]
    finally:
        connection.close()


def _reader(
    database: Path, identities: dict[str, str], **replays: a3.CaseReplay
) -> a3.A3Reader:
    first, second = _case_ids(database)
    r1 = _replay(
        [_r1_combination(series=_part(C, identities["series"], items=[_series(NAME)]))],
        {
            "title": _prior("keep", selected=identities["title"]),
            "series": _prior(
                "propose_change",
                selected=identities["series"],
                assessment="fills_empty",
            ),
            "subtitle": _prior("keep"),
        },
    )
    masked = _replay(
        [
            _combination(
                TITLE,
                kind=C,
                alternative="x",
                series=_part(C, "y", items=[_series(NAME)]),
            )
        ],
        {"title": _prior("unresolved_choice", reasons=["competing_evidence"])},
        ("title",),
    )
    cases = {first: replays.get("first", r1), second: replays.get("second", masked)}

    def reader(_root: Path, _database: Path) -> a3.A3Probe:
        return a3.A3Probe(POLICY, False, 256, cases, 0)

    return reader


_ELECTIONS = frozenset(commit for _label, commit, _ in ledger.LATER_ELECTIONS)


def _source_predates_elections(commit: str, _revision: str) -> bool:
    return commit not in _ELECTIONS and commit != a3.WITHHELD_TITLE_RULE


def _a3_run(
    tmp_path: Path,
    *extra: str,
    ancestry: ledger.Ancestry = _source_predates_elections,
    extra_shadow: str | None = None,
    **replays: a3.CaseReplay,
) -> tuple[int, str, str]:
    text = Sentinels()
    fixture, identities = _a3_fixture(text)
    if extra_shadow is not None:
        fixture.shadow.append(
            {
                "destination": "title",
                "disposition": "conflict",
                "reason_code": extra_shadow,
            }
        )
    database = _database(
        tmp_path / "a3.sqlite3", "v26-like", [fixture, _masked_fixture(text)]
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ledger.main(
        [
            *_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
            "--a3-combination",
            *extra,
        ],
        stdout=stdout,
        stderr=stderr,
        ancestry=ancestry,
        attention=lambda _root, _db: ledger.AttentionProbe((), 0, 0, SCALAR, True),
        combination_replay=_reader(database, identities, **replays),
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_the_analysis_splits_the_cost_and_screens_the_relaxation(
    tmp_path: Path,
) -> None:
    code, stdout, stderr = _a3_run(tmp_path)
    assert (code, stderr) == (0, "")
    section = json.loads(stdout)["a3_combination"]
    (entry,) = section["snapshots"]
    assert entry["withheld_title_rule_at_source"] is False
    assert (entry["works"], entry["stale_works"], entry["mirror_disagreements"]) == (
        2,
        0,
        0,
    )
    populations = {row["destination"]: row for row in entry["populations"]}
    title, series = populations["title"], populations["series"]
    # Classed by the outcome before the overwrite, never by the stored one.
    assert (title["blocked"], title["kept_value"], title["held_first"]) == (2, 1, 1)
    assert (series["blocked"], series["value_change"], series["fills"]) == (1, 1, 1)
    # A masked competing title on a source that predates the elections.
    assert title["held_first_later_election"] == 1
    assert title["atomicity"] == 1
    assert entry["held_first_reasons"] == [
        {"destination": "title", "reason": "competing_evidence", "fields": 1}
    ]
    causes = {row["cause"]: row for row in entry["causes"]}
    assert causes["series_name_in_title"]["works"] == 2
    assert causes["series_name_in_title"]["fields"] == 3
    rules = {row["rule"]: row for row in entry["relaxations"]}
    r1 = rules[a3.R1]
    assert (r1["outcomes"]["admitted"], r1["outcomes"]["refused_masked"]) == (1, 1)
    assert (r1["fields"], r1["true_positive"], r1["false_positive"]) == (1, 1, 0)
    assert r1["field_classes"]["discriminable"] == 1 and r1["pick_agrees"] == 1
    assert r1["containment"]["series_name_in_title"] == 1
    # R3 admits no value; the kept title's sibling fill stays held.
    r3 = rules[a3.R3]
    assert (r3["fields"], r3["cases"], r3["outcomes"]["admitted"]) == (0, 0, 0)
    pooled = {row["rule"]: row for row in section["pooled_relaxations"]}
    assert pooled[a3.R2]["n"] == 0
    assert pooled[a3.R2]["precision"] is None
    assert pooled[a3.R2]["wilson_lower_bound"] is None


def test_a_source_with_the_elections_has_no_stale_competing_title(
    tmp_path: Path,
) -> None:
    code, stdout, _ = _a3_run(tmp_path, ancestry=lambda _commit, _revision: True)
    assert code == 0
    (entry,) = json.loads(stdout)["a3_combination"]["snapshots"]
    title = next(row for row in entry["populations"] if row["destination"] == "title")
    assert (title["held_first"], title["held_first_later_election"]) == (1, 0)
    assert entry["withheld_title_rule_at_source"] is True


@pytest.mark.parametrize(
    ("alternatives", "stale"),
    [(256, True), (257, False), (1, True)],
    ids=["at-the-limit", "over-the-limit", "one-survivor"],
)
def test_main_forms_nothing_when_too_many_combinations_survive(
    alternatives: int, stale: bool
) -> None:
    combinations = [
        _same_name(series=_part(C, f"series-{index}", items=[_series(TITLE)]))
        for index in range(alternatives)
    ]
    # A repeated combination is one combination, as main keys them.
    combinations.append(combinations[0])
    replay = _replay(combinations, {"title": _prior("keep")})
    assert a3.stale_for_main(replay) is stale


def test_main_forms_nothing_when_no_combination_survives() -> None:
    assert a3.stale_for_main(_replay([_r1_combination()], {})) is False


def test_r3_counts_a_case_only_when_no_sibling_stays_held(tmp_path: Path) -> None:
    preserved = _replay(
        [_r1_combination()],
        {
            "title": _prior("keep", all_preserve=True),
            "series": _prior("keep", all_preserve=True),
        },
    )
    code, stdout, _ = _a3_run(tmp_path, first=preserved)
    assert code == 0
    (entry,) = json.loads(stdout)["a3_combination"]["snapshots"]
    r3 = next(row for row in entry["relaxations"] if row["rule"] == a3.R3)
    assert (r3["outcomes"]["admitted"], r3["cases"]) == (2, 1)
    held = _replay(
        [_r1_combination()],
        {
            "title": _prior("keep", all_preserve=True),
            "series": _prior("propose_change", selected="s", assessment="fills_empty"),
        },
    )
    (tmp_path / "held").mkdir()
    code, stdout, _ = _a3_run(tmp_path / "held", first=held)
    (entry,) = json.loads(stdout)["a3_combination"]["snapshots"]
    r3 = next(row for row in entry["relaxations"] if row["rule"] == a3.R3)
    assert (r3["outcomes"]["admitted"], r3["cases"]) == (1, 0)


def test_a_work_main_would_form_is_stale_and_not_relaxed(tmp_path: Path) -> None:
    # Two combinations, vetoed at a source without the same-name rule; only
    # one survives main's rule, which is enough.
    stale = _replay(
        [_r1_combination(), _same_name()],
        {"title": _prior("keep"), "series": _prior("propose_change")},
    )
    code, stdout, _ = _a3_run(tmp_path, first=stale)
    assert code == 0
    (entry,) = json.loads(stdout)["a3_combination"]["snapshots"]
    # The mirror reads the source's own rule, so it agrees with the veto.
    assert entry["mirror_disagreements"] == 0
    populations = {row["destination"]: row for row in entry["populations"]}
    assert populations["title"]["stale_later_rule"] == 1
    assert populations["series"]["stale_later_rule"] == 1
    assert populations["series"]["value_change"] == 0
    assert entry["stale_works"] == 1
    causes = {row["cause"]: row["works"] for row in entry["causes"]}
    assert causes["series_name_equals_title"] == 1
    for row in entry["relaxations"]:
        assert row["outcomes"]["main_forms"] >= 1, row["rule"]


@pytest.mark.parametrize(
    "replay",
    [
        dataclasses.replace(
            _replay([_r1_combination()], _r1_prior()), counterfactual="differs"
        ),
        dataclasses.replace(
            _replay([_r1_combination()], _r1_prior()), counterfactual="failed"
        ),
        dataclasses.replace(
            _replay([_r1_combination()], _r1_prior()), replay="differs"
        ),
        dataclasses.replace(_replay([_r1_combination()], _r1_prior()), a3=("title",)),
    ],
    ids=["outside-a3", "counterfactual-failed", "replay-differs", "other-fields"],
)
def test_any_difference_outside_the_a3_fields_refuses(
    tmp_path: Path, replay: a3.CaseReplay
) -> None:
    code, stdout, stderr = _a3_run(tmp_path, first=replay)
    assert (code, stdout) == (2, "")
    assert stderr.startswith("admission-rule-ledger: ")
    assert _SENTINEL not in stderr


def test_a_mirror_disagreement_is_counted(tmp_path: Path) -> None:
    wrong = dataclasses.replace(_r1_combination(), verdict=False)
    replay = _replay([wrong, _r1_combination()], _r1_prior())
    code, stdout, _ = _a3_run(tmp_path, first=replay)
    (entry,) = json.loads(stdout)["a3_combination"]["snapshots"]
    assert (code, entry["mirror_disagreements"], entry["combinations_tried"]) == (
        0,
        1,
        3,
    )


def test_the_markdown_is_the_analysis_only_and_reads_not_estimable(
    tmp_path: Path,
) -> None:
    code, markdown, _ = _a3_run(tmp_path, "--format", "markdown")
    assert code == 0
    assert markdown.startswith("### Replay\n")
    assert "## Ranked gates" not in markdown
    assert "| snap-a | title | 2 | 0 | 0 (0 / 0) | 1 | 1 (1) | 1 |" in markdown
    assert "| snap-a | series name in title | 2 | 3 | 2 |" in markdown
    assert (
        "| same_name_series_beside_new_title | pooled | 2 | 0 | 1 / 1 / 0 / 0 / 0 / 0 / 0 "
        "| 0 | 0 | not applicable | 0/0/0 | not estimable |"
    ) in markdown
    assert "| snap-a | 2 | 0 | 2 | no |" in markdown


def test_the_analysis_is_aggregate_only(tmp_path: Path) -> None:
    code, stdout, stderr = _a3_run(tmp_path)
    assert code == 0
    assert _SENTINEL not in stdout and _SENTINEL not in stderr
    keys: list[str] = []
    values: list[str] = []
    _strings(json.loads(stdout), keys, values)
    vocabulary = _test_vocabulary(["snap-a"]) | a3.VOCABULARY | {POLICY}
    vocabulary |= ledger._STRUCTURAL_KEYS | {"rich_refusals_collapsed"}
    vocabulary |= set(ledger.REASONS)
    vocabulary |= {label for label, _commit, _ in ledger.LATER_ELECTIONS}
    for value in [*keys, *values]:
        assert value in vocabulary or len(value) == 40, value
    (tmp_path / "m").mkdir()
    markdown = _a3_run(tmp_path / "m", "--format", "markdown")[1]
    assert _SENTINEL not in markdown and "ALPHA" not in markdown


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        json.dumps({"engine": {"policy": POLICY, "same_name_rule": True}}),
        json.dumps(
            {
                "engine": {"policy": _SENTINEL, "same_name_rule": True, "limit": 256},
                "cases": {},
                "failed": 0,
            }
        ),
        json.dumps(
            {
                "engine": {"policy": POLICY, "same_name_rule": True, "limit": 256},
                "cases": {
                    "c": {
                        "replay": _SENTINEL,
                        "a3": [],
                        "combinations": [],
                        "counterfactual": None,
                        "prior": {},
                    }
                },
                "failed": 0,
            }
        ),
    ],
    ids=["unparsable", "missing-cases", "unknown-policy", "unknown-replay-state"],
)
def test_a_surprising_replay_document_is_a_fixed_refusal(document: str) -> None:
    with pytest.raises(ledger.Refusal) as refused:
        a3.parse_probe(document)
    assert _SENTINEL not in refused.value.message


def _product_case(tmp_path: Path) -> Path:
    """A product-built case: a kept title that holds the series name."""
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    report = _bibliographic_report(
        current_title="Quillmarsh Vantry Codex",
        providers=(
            _bibliographic_source(
                "catalogue",
                "edition-1",
                titles=("Quillmarsh Vantry Codex",),
                series="Quillmarsh",
            ),
        ),
    )
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    repository.close()
    return database


def test_the_child_replays_a_product_case_and_the_mirror_agrees(tmp_path: Path) -> None:
    probe = a3.child_a3(REPOSITORY, _product_case(tmp_path))
    assert probe.same_name_rule and probe.failed == 0 and len(probe.cases) == 1
    (replay,) = probe.cases.values()
    assert (replay.replay, replay.a3, replay.counterfactual) == (
        "equal",
        ("title", "series"),
        "equal",
    )
    assert replay.combinations
    assert all(
        item.verdict == bool(a3.clauses(item, same_name_rule=True))
        for item in replay.combinations
    )
    # The source passes its own kept-title fact to the duplicate check.
    assert all(item.title_kept for item in replay.combinations)
    assert a3.classify(replay.prior["title"]) == "kept_value"
    assert a3.classify(replay.prior["series"]) == "value_change"
    assert a3.work_causes(replay, probe.limit, same_name_rule=True) == (
        "series_name_in_title",
    )
    assert not a3.stale_for_main(replay)
    assert a3.relax_r1(replay, _case()).admitted == ("series",)


def test_the_child_reports_a_stored_set_altered_off_the_a3_fields(
    tmp_path: Path,
) -> None:
    database = _product_case(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute("DROP TRIGGER proposal_sets_no_update")
    ((identity, text),) = connection.execute(
        "SELECT proposal_set_id, document FROM proposal_sets"
    ).fetchall()
    document = json.loads(text)
    other = next(
        result
        for result in document["results"]
        if result["destination"] not in a3.BIBLIOGRAPHIC
    )
    other["reasons"] = [*other["reasons"], "competing_evidence"]
    connection.execute(
        "UPDATE proposal_sets SET document=? WHERE proposal_set_id=?",
        (json.dumps(document), identity),
    )
    connection.commit()
    connection.close()
    probe = a3.child_a3(REPOSITORY, database)
    (replay,) = probe.cases.values()
    # The replay and the negative control both see the altered field.
    assert (replay.replay, replay.a3, replay.counterfactual) == (
        "differs",
        ("title", "series"),
        "differs",
    )


def test_the_child_reads_a_source_without_the_same_name_rule(
    tmp_path: Path,
) -> None:
    """A source whose duplicate check takes no kept-title fact (before #1099)."""
    database = _product_case(tmp_path)
    old = tmp_path / "old"
    shutil.copytree(
        REPOSITORY / "src", old / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    module = old / "src" / "mechanical_librarian" / "domain" / "proposal_set.py"
    text = module.read_text(encoding="utf-8")
    signature = (
        "    results: dict[TargetDestination, ProposalResult],\n"
        "    title_kept: bool,\n"
        ") -> bool:\n"
        "    alternatives = {"
    )
    call = (
        "_duplicates_bibliographic_component(selections, targets, results, title_kept)"
    )
    assert text.count(signature) == 1 and text.count(call) == 1
    text = text.replace(
        signature,
        "    results: dict[TargetDestination, ProposalResult],\n"
        ") -> bool:\n"
        "    title_kept = False\n"
        "    alternatives = {",
    ).replace(call, "_duplicates_bibliographic_component(selections, targets, results)")
    module.write_text(text, encoding="utf-8")
    probe = a3.child_a3(old, database)
    assert probe.same_name_rule is False
    (replay,) = probe.cases.values()
    assert replay.replay == "equal" and replay.combinations
    # Without the parameter the kept-title fact comes from the title's outcome.
    assert all(item.title_kept for item in replay.combinations)
    assert all(
        item.verdict == bool(a3.clauses(item, same_name_rule=False))
        for item in replay.combinations
    )


@pytest.mark.parametrize(
    ("relax", "prior"),
    [
        (
            a3.relax_r1,
            {
                **_r1_prior(),
                "series": _prior(
                    "acceptable_choice", selected="fill", assessment="fills_empty"
                ),
            },
        ),
        (
            a3.relax_r2,
            {**_r2_prior(), "title": _prior("acceptable_choice", selected="new")},
        ),
    ],
    ids=["r1-acceptable-series", "r2-acceptable-title"],
)
def test_an_acceptable_choice_is_not_a_proposed_change(
    relax: Callable[[a3.CaseReplay, ledger.Case], a3.Verdict],
    prior: dict[str, a3.Prior],
) -> None:
    combinations = [_r1_combination(), _r2_combination()]
    assert relax(_replay(combinations, prior), _case()).outcome == "refused_shape"


def test_one_surviving_combination_is_not_explained() -> None:
    survivor = dataclasses.replace(_r1_combination(), verdict=False)
    assert a3.work_causes(_replay([survivor], {}), 256, same_name_rule=False) == (
        "not_explained",
    )


def test_only_competing_values_count_as_stale_by_election_and_only_its_code_as_atomicity(
    tmp_path: Path,
) -> None:
    other = _replay(
        [
            _combination(
                TITLE,
                kind=C,
                alternative="x",
                series=_part(C, "y", items=[_series(NAME)]),
            )
        ],
        {"title": _prior("defer", reasons=["no_supported_evidence"])},
        ("title",),
    )
    code, stdout, _ = _a3_run(tmp_path, second=other, extra_shadow="no_eligible_action")
    assert code == 0
    (entry,) = json.loads(stdout)["a3_combination"]["snapshots"]
    title = next(row for row in entry["populations"] if row["destination"] == "title")
    assert (title["held_first"], title["held_first_later_election"]) == (1, 0)
    assert title["atomicity"] == 1


def _table_widths(markdown: str) -> list[tuple[int, int]]:
    """Header and delimiter cell counts of every table in the text."""
    lines = markdown.splitlines()
    return [
        (lines[index - 1].count(" | ") + 1, line.count("---|"))
        for index, line in enumerate(lines)
        if line.startswith("|---")
    ]


def test_every_rendered_table_has_a_delimiter_as_wide_as_its_header(
    tmp_path: Path,
) -> None:
    code, markdown, _ = _a3_run(tmp_path, "--format", "markdown")
    assert code == 0
    (tmp_path / "a6").mkdir()
    a6_code, a6_markdown, _ = _breakdown_run(tmp_path / "a6", "--format", "markdown")
    database = _database(tmp_path / "l.sqlite3", "v26-like", [])
    ledger_code, ledger_markdown, _ = _run(
        _arguments(
            [("snap-a", database, EBOOK_REVISION)], tmp_path, "--format", "markdown"
        )
    )
    assert (a6_code, ledger_code) == (0, 0)
    for text in (markdown, a6_markdown, ledger_markdown):
        widths = _table_widths(text)
        assert widths and all(header == cells for header, cells in widths), widths
