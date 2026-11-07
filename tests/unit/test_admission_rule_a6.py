"""The A6 abstention analysis of the admission-rule ledger (#1745).

Synthetic only: packets are built here as the child would report them, and
every free-text slot carries a sentinel, so a leaked value shows up in the
output. One test replays a product-built database through the real child.
"""

from __future__ import annotations

import dataclasses
import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
import scripts.admission_rule_a6 as a6
import scripts.admission_rule_ledger as ledger

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain import shadow_decision
from mechanical_librarian.domain.proposal_set import evaluate_report
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

V22 = "deterministic-shadow-decision-policy-v22"
SCALAR = ("authors", "language", "narrators", "publishedDate", "publisher", "title")


def _candidate(text: Sentinels, **overrides: object) -> a6.Candidate:
    """A candidate that passes every v22 check for an edition destination."""
    base = a6.Candidate(
        candidate_id=text(),
        value={"kind": "text", "value": text()},
        scope="edition",
        date_event=None,
        date_precision=None,
        assessment="replaces_target",
        evidence_references=(text(),),
        evidence_scope="edition",
        binding="established",
        recording=False,
        narrator_agreement=False,
        independent_title=False,
        publication_event=None,
        publication_consistency=None,
        has_publication=False,
        local=1,
        correlations=1,
        preservation=0,
        action=True,
        citations_present=True,
        citations_known=True,
        combinations_known=True,
        eligible=True,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def _work(text: Sentinels, **overrides: object) -> a6.Candidate:
    work: dict[str, object] = {
        "scope": "work",
        "evidence_scope": "work",
        "binding": "associated_work_only",
    }
    return _candidate(text, **{**work, **overrides})


def _proposal(
    text: Sentinels,
    destination: str,
    candidates: Sequence[a6.Candidate],
    *,
    reason: str | None = "insufficient_support",
    **overrides: object,
) -> a6.Proposal:
    base = a6.Proposal(
        destination=destination,
        outcome="propose_change",
        reasons=("unique_typed_candidate",),
        current={"kind": "text", "value": text()},
        sufficiency="candidate",
        preservation=0,
        report_only=destination in shadow_decision._REPORT_ONLY_SOURCE_DESTINATIONS,
        consensus_title=False,
        tidy_title=False,
        evidence_led=False,
        reason=reason,
        candidates=tuple(candidates),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


# --- Sub-check attribution --------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "first"),
    [
        ({"scope": "edition", "local": 0, "correlations": 0}, "scope"),
        ({"binding": "established", "local": 0}, "binding"),
        ({"citations_known": False, "local": 0}, "citations"),
        ({"preservation": 1, "local": 0}, "preservation"),
        ({"local": 0, "correlations": 0}, "support_local"),
        ({"correlations": 0}, "support_correlation"),
        ({}, "passes"),
    ],
    ids=[
        "scope-before-support",
        "binding-before-support",
        "citations-before-support",
        "preservation-before-support",
        "local-before-correlation",
        "correlation-only",
        "every-check-passes",
    ],
)
def test_sub_check_attribution_names_the_first_failing_check(
    overrides: dict[str, object], first: str
) -> None:
    text = Sentinels()
    # Title is a work destination; each case breaks the named check and every
    # later one it lists.
    candidate = _work(text, eligible=first == "passes", **overrides)
    proposal = _proposal(text, "title", [candidate])
    states, compared, disagreed = a6.attribute(proposal, "v22")
    assert states == [first]
    assert (compared, disagreed) == (1, 0)


def test_publication_and_narrator_checks_are_named() -> None:
    text = Sentinels()
    date = _candidate(
        text,
        date_event="manifestation_publication",
        date_precision="day",
        has_publication=True,
        publication_event="manifestation_publication",
        publication_consistency="conflicting",
        correlations=2,
        eligible=False,
    )
    assert a6.attribute(_proposal(text, "publishedDate", [date]), "v22")[0] == [
        "publication"
    ]
    narrator = _candidate(text, eligible=False)
    assert a6.attribute(_proposal(text, "narrators", [narrator]), "v22")[0] == [
        "narrator"
    ]


def test_the_v9_table_reads_its_own_support_rules() -> None:
    text = Sentinels()
    # v9 authors are report-only: one local item is enough and correlations
    # are never counted; v22 replaced that with the two-correlation rule.
    weak = _work(text, local=0, correlations=1, eligible=False)
    proposal = _proposal(text, "authors", [weak], report_only=True)
    assert a6.failing_checks(proposal, weak, "v9", report_only=True) == (
        "support_local",
    )
    assert a6.failing_checks(proposal, weak, "v22", report_only=False) == (
        "support_correlation",
    )
    local = _work(text, local=1, correlations=0, eligible=True)
    assert a6.attribute(
        _proposal(text, "authors", [local], report_only=True), "v9"
    ) == (
        ["passes"],
        1,
        0,
    )


def test_a_mirror_that_disagrees_with_the_source_verdict_is_counted() -> None:
    text = Sentinels()
    # The source said eligible; the mirror finds a failing check.
    candidate = _work(text, local=0, correlations=0, eligible=True)
    states, compared, disagreed = a6.attribute(
        _proposal(text, "title", [candidate]), "v22"
    )
    assert states == ["support_local"]
    assert (compared, disagreed) == (1, 1)


def test_candidate_and_field_states_are_not_checks() -> None:
    text = Sentinels()
    assert a6.attribute(_proposal(text, "title", []), "v22")[0] == ["no_candidate"]
    no_action = _work(text, action=False, eligible=None)
    assert a6.attribute(_proposal(text, "title", [no_action]), "v22") == (
        ["no_action"],
        0,
        0,
    )
    manual = _proposal(text, "title", [_work(text, local=0)], sufficiency="manual")
    assert a6.attribute(manual, "v22")[0] == ["not_evaluated"]
    conflict = _proposal(
        text, "language", [_candidate(text)], reasons=("field_policy_conflict",)
    )
    assert a6.attribute(conflict, "v22")[0] == ["language_conflict"]
    assert a6.attribute(_proposal(text, "title", [_work(text)]), None)[0] == [
        "not_mirrored"
    ]


def test_the_checkout_engine_is_mirrored_by_the_v23_table() -> None:
    # A policy-version bump on main must revisit the mirror (and the
    # relaxations, which run under it).
    assert a6.table_for(shadow_decision.DECISION_POLICY_VERSION) == "v23"
    assert a6.table_for("deterministic-shadow-decision-policy-v22") == "v22"
    assert a6.table_for("deterministic-shadow-decision-policy-v23") == "v23"
    assert a6.table_for("deterministic-shadow-decision-policy-v9") == "v9"
    assert a6.table_for("deterministic-shadow-decision-policy-v12") is None
    # Outside the mirrored range: a later engine is never read as v22.
    assert a6.table_for("deterministic-shadow-decision-policy-v21") is None
    assert a6.table_for("deterministic-shadow-decision-policy-v28") is None


def test_a_degraded_candidate_fails_its_own_check_from_v23() -> None:
    text = Sentinels()
    # A well-supported narrator credit in the authors field, beside a clean
    # author with too little support. From v23 the source refuses the credit
    # as degraded (#1283) before any other check.
    credit = _work(
        text,
        value={"kind": "text_list", "value": ["Read by"]},
        local=1,
        correlations=2,
        eligible=False,
    )
    clean = _work(text, local=0, correlations=1, eligible=False)
    proposal = _proposal(text, "authors", [credit, clean])
    assert a6.attribute(proposal, "v23") == (
        ["degraded", "support_correlation"],
        2,
        0,
    )
    # v22 had no degraded refusal: there the credit passes every check, so a
    # v22 verdict of eligible agrees and this one would not.
    assert a6.failing_checks(proposal, credit, "v22", report_only=False) == ()
    assert "degraded" in a6.VOCABULARY


# --- Relaxations ------------------------------------------------------------


def test_a_relaxation_admits_the_unique_candidate_that_fails_only_its_threshold() -> (
    None
):
    text = Sentinels()
    admitted = _work(text, local=0, correlations=2, eligible=False)
    short = _work(text, local=0, correlations=1, eligible=False)
    proposal = _proposal(text, "title", [admitted, short])
    assert a6.relax(proposal) == ("admitted", admitted)


@pytest.mark.parametrize(
    "count", [0, 2], ids=["no-candidate-qualifies", "two-candidates-qualify"]
)
def test_a_relaxation_refuses_zero_or_several_candidates(count: int) -> None:
    text = Sentinels()
    candidates = [
        _work(text, local=0, correlations=2, eligible=False) for _ in range(count)
    ]
    candidates.append(_work(text, local=0, correlations=1, eligible=False))
    verdict, admitted = a6.relax(_proposal(text, "title", candidates))
    assert admitted is None
    assert verdict == ("refused_none" if count == 0 else "refused_ambiguous")


def test_a_relaxation_refuses_a_degraded_candidate() -> None:
    text = Sentinels()
    credit = _work(
        text,
        value={"kind": "text_list", "value": ["Read by"]},
        local=0,
        correlations=1,
        eligible=False,
    )
    verdict, admitted = a6.relax(_proposal(text, "authors", [credit]))
    assert (verdict, admitted) == ("refused_degraded", None)


@pytest.mark.parametrize(
    "reason",
    [
        "target_only_preservation",
        "ambiguous_eligible_actions",
        "bibliographic_combination_unresolved",
        "isbn_title_material_conflict",
        "evidence_sufficiency_manual",
        "no_eligible_action",
    ],
    ids=[
        "target-only-preservation",
        "ambiguous",
        "combination-unresolved",
        "isbn-title-conflict",
        "upstream-echo",
        "no-eligible-action",
    ],
)
def test_a_relaxation_runs_only_on_insufficient_support(reason: str) -> None:
    text = Sentinels()
    candidate = _work(text, local=0, correlations=2, eligible=False)
    verdict, admitted = a6.relax(_proposal(text, "title", [candidate], reason=reason))
    assert (verdict, admitted) == ("refused_reason", None)


def test_a_language_conflict_and_a_passing_candidate_are_not_relaxed() -> None:
    text = Sentinels()
    language = _candidate(text, local=0, correlations=2, eligible=False)
    conflict = _proposal(
        text, "language", [language], reasons=("field_policy_conflict",)
    )
    assert a6.relax(conflict) == ("refused_conflict", None)
    passing = _work(text, eligible=True)
    assert a6.relax(_proposal(text, "title", [passing])) == ("main_passes", None)


@pytest.mark.parametrize(
    ("destination", "candidate"),
    [
        ("authors", {"local": 0, "correlations": 1}),
        ("publisher", {"local": 0, "correlations": 2}),
        ("language", {"local": 0, "correlations": 2}),
        (
            "publishedDate",
            {
                "local": 1,
                "correlations": 1,
                "date_event": "manifestation_publication",
                "date_precision": "day",
                "has_publication": True,
                "publication_event": "manifestation_publication",
                "publication_consistency": "consistent",
            },
        ),
    ],
    ids=["authors", "publisher", "language", "published-date"],
)
def test_each_relaxation_admits_its_one_step(
    destination: str, candidate: dict[str, object]
) -> None:
    text = Sentinels()
    make = _work if destination == "authors" else _candidate
    admitted = make(text, eligible=False, **candidate)
    verdict, chosen = a6.relax(_proposal(text, destination, [admitted]))
    assert (verdict, chosen) == ("admitted", admitted)
    # One step only: no correlation at all is still refused.
    none = make(text, eligible=False, **{**candidate, "correlations": 0})
    assert a6.relax(_proposal(text, destination, [none]))[0] == "refused_none"


@pytest.mark.parametrize(
    ("destination", "candidate"),
    [
        ("title", {"scope": "edition", "local": 0, "correlations": 2}),
        ("title", {"binding": "established", "local": 0, "correlations": 2}),
        ("title", {"local": 2, "correlations": 2}),
        (
            "publishedDate",
            {
                "local": 1,
                "correlations": 1,
                "date_event": "manifestation_publication",
                "date_precision": "month",
                "has_publication": True,
                "publication_event": "manifestation_publication",
                "publication_consistency": "consistent",
            },
        ),
    ],
    ids=[
        "also-fails-scope",
        "also-fails-binding",
        "title-with-local-evidence",
        "date-at-month-precision",
    ],
)
def test_a_relaxation_refuses_a_candidate_outside_its_one_step(
    destination: str, candidate: dict[str, object]
) -> None:
    text = Sentinels()
    make = _work if destination == "title" else _candidate
    refused = make(text, eligible=False, **candidate)
    assert a6.relax(_proposal(text, destination, [refused])) == ("refused_none", None)


def test_relaxation_names_are_closed_and_one_per_scalar_destination() -> None:
    destinations = [rule.destination for rule in a6.RELAXATIONS]
    assert len(destinations) == len(set(destinations))
    assert set(destinations) <= shadow_decision._SUPPORTED_DESTINATIONS
    # Narrators stay with recording binding (W6/W7).
    assert "narrators" not in destinations
    assert {rule.rule for rule in a6.RELAXATIONS} <= a6.VOCABULARY


# --- Screens ----------------------------------------------------------------


def _screen_case(text: Sentinels) -> tuple[ledger.Case, str, str, str]:
    """A case with an accepted cited record and an accepted uncited one."""
    fixture = Fixture(text, "v26-like")
    first, second = fixture.provider(), fixture.provider()
    work = text()
    cited, cited_claim = fixture.record(first, work)
    other, _other_claim = fixture.record(second, work)
    fixture.bind(first, cited, work, "accepted")
    fixture.bind(second, other, work, "accepted")
    case = ledger._build_case(
        {"results": fixture.results}, fixture.report, {"outcomes": []}, {}
    )
    providers = case.providers()
    cited_value = cast(
        "str",
        ledger._dicts(ledger._dicts(providers[first]["candidates"])[0]["claims"])[0][
            "value"
        ],
    )
    other_value = cast(
        "str",
        ledger._dicts(ledger._dicts(providers[second]["candidates"])[0]["claims"])[0][
            "value"
        ],
    )
    return case, cited_claim, cited_value, other_value


def test_value_agreement_never_counts_a_record_the_candidate_cites() -> None:
    text = Sentinels()
    case, cited_claim, cited_value, other_value = _screen_case(text)
    # Agreeing only with its own citation: the uncited record disagrees.
    own = _work(
        text,
        value={"kind": "text", "value": cited_value},
        evidence_references=(cited_claim,),
    )
    assert a6.value_agreement(case, "title", own) is False
    # Agreeing with the uncited accepted record counts.
    agreeing = _work(
        text,
        value={"kind": "text", "value": other_value.lower()},
        evidence_references=(cited_claim,),
    )
    assert a6.value_agreement(case, "title", agreeing) is True
    # Nothing to compare for a destination no accepted record states.
    assert a6.value_agreement(case, "publisher", agreeing) is None


def _bare_case(
    results: dict[str, dict[str, object]], labels: dict[str, str]
) -> ledger.Case:
    """A case whose provider records carry the given labels, nothing else."""
    return ledger.Case(
        medium="ebook",
        epub=False,
        report={},
        results=results,
        shadow={},
        cover_candidates=[],
        cover_omissions=set(),
        records={record: (0, "work", False) for record in labels},
        labels=dict(labels),
    )


def _alternative(identity: str, *records: str) -> dict[str, object]:
    return {"alternative_id": identity, "evidence_references": list(records)}


def test_the_screen_counts_false_positives_and_preserved_values() -> None:
    text = Sentinels()
    right, wrong = text(), text()
    case = _bare_case(
        {"title": {"alternatives": [_alternative("chosen", right)]}},
        {right: "positive", wrong: "negative"},
    )
    current = {"kind": "text", "value": text()}
    candidate = _work(
        text, candidate_id="chosen", value=current, evidence_references=(right, wrong)
    )
    proposal = _proposal(text, "title", [candidate], current=current)
    tally = a6.RelaxationTally()
    a6._screen(case, proposal, candidate, tally)
    assert (tally.true_positive, tally.false_positive, tally.unlabelled) == (1, 1, 0)
    assert tally.preserves_current == 1
    assert (dict(tally.classes), tally.agrees) == ({"discriminable": 1}, 1)
    # A value the field does not hold already is not counted as preserving.
    changed = dataclasses.replace(candidate, value={"kind": "text", "value": text()})
    other = a6.RelaxationTally()
    a6._screen(case, proposal, changed, other)
    assert other.preserves_current == 0


@pytest.mark.parametrize(
    ("alternatives", "expected"),
    [
        ([("a", "pos"), ("b", "pos")], ("tied", False)),
        ([("a", "pos"), ("b", "neg")], ("discriminable", True)),
        ([("b", "pos"), ("a", "neg")], ("discriminable", False)),
        ([("a", "neg"), ("b", "unlabelled")], ("no_right_work", False)),
    ],
    ids=["two-right-alternatives", "admitted-is-right", "admitted-is-wrong", "none"],
)
def test_field_class_reads_ao_classes_and_the_right_pick(
    alternatives: list[tuple[str, str]], expected: tuple[str, bool]
) -> None:
    text = Sentinels()
    labels = {"pos": "positive", "neg": "negative", "unlabelled": "unlabelled"}
    records = {kind: text() for kind in labels}
    case = _bare_case(
        {
            "title": {
                "alternatives": [
                    _alternative(identity, records[kind])
                    for identity, kind in alternatives
                ]
            }
        },
        {records[kind]: label for kind, label in labels.items()},
    )
    candidate = _work(text, candidate_id="a")
    assert a6.field_class(case, "title", candidate) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "none"), (1, "one"), (2, "more"), (3, "more")],
    ids=["no-alternative", "one-alternative", "two-alternatives", "three"],
)
def test_alternatives_are_counted_none_one_or_more(count: int, expected: str) -> None:
    alternatives = [_alternative(str(index)) for index in range(count)]
    case = _bare_case({"title": {"alternatives": alternatives}}, {})
    assert a6._alternatives(case, "title") == expected


def test_the_echo_and_rich_residual_keep_to_their_own_fields() -> None:
    results: dict[str, dict[str, object]] = {
        # Scalar and blocked, no eligible action: A6a's blocked population.
        "title": {"outcome": "defer"},
        # Scalar, the upstream echo, but proposed: not an echo of a refusal.
        "publisher": {"outcome": "propose_change"},
        # Scalar, blocked, the upstream echo.
        "authors": {"outcome": "defer"},
        # Rich, no eligible action: the rich residual.
        "series": {"outcome": "defer"},
    }
    shadow = {
        "outcomes": [
            {
                "destination": destination,
                "disposition": "abstention",
                "reason_code": reason,
                "policy_destination": "automatic",
            }
            for destination, reason in [
                ("title", "no_eligible_action"),
                ("publisher", "evidence_sufficiency_manual"),
                ("authors", "evidence_sufficiency_manual"),
                ("series", "no_eligible_action"),
            ]
        ]
    }
    probe = a6.A6Probe(V22, frozenset({"title", "publisher", "authors"}), True, {}, 0)
    tally = a6.A6Tally()
    a6._measure_case(tally, _bare_case(results, {}), shadow, probe, "v22", None)
    assert dict(tally.rich_residual) == {"series": 1}
    assert dict(tally.echo) == {("authors", "ebook"): 1}
    assert set(tally.populations) == {
        ("blocked", "title", "no_eligible_action", "ebook", "none")
    }


# --- The breakdown end to end -----------------------------------------------


def _breakdown_fixture(text: Sentinels) -> tuple[Fixture, dict[str, str]]:
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, claim = fixture.record(provider, work)
    fixture.bind(provider, accepted, work, "accepted")
    # Proposable, automatic, abstained: the abstention's own cost.
    fixture.result("title", "propose_change", ["unique_typed_candidate"], [claim])
    fixture.shadow.append(
        {
            "destination": "title",
            "disposition": "abstention",
            "reason_code": "insufficient_support",
            "policy_destination": "automatic",
            "candidate_id": text(),
        }
    )
    # Proposable but owner-reserved by policy: not A6's cost.
    fixture.result("publisher", "propose_change", ["unique_typed_candidate"], [claim])
    fixture.shadow.append(
        {
            "destination": "publisher",
            "disposition": "abstention",
            "reason_code": "insufficient_support",
            "policy_destination": "manual",
            "candidate_id": text(),
        }
    )
    # Blocked without an alternative: the blocked population, class "none".
    fixture.result("language", "defer", ["no_supported_evidence"])
    fixture.abstain("language", "no_eligible_action")
    # The echo is reported, never a population.
    fixture.result("authors", "defer", ["no_positive_preservation_evidence"])
    fixture.abstain("authors", "evidence_sufficiency_manual")
    title = fixture.results[0]
    alternative = ledger._dicts(title["alternatives"])[0]
    return fixture, {
        "alternative": cast("str", alternative["alternative_id"]),
        "claim": claim,
    }


def _replay(text: Sentinels, database: Path, identities: dict[str, str]) -> a6.A6Reader:
    connection = ledger._open_snapshot(database)
    case_id = cast(
        "str",
        connection.execute("SELECT review_case_id FROM review_cases").fetchone()[0],
    )
    connection.close()
    title = _proposal(
        text,
        "title",
        [
            _work(
                text,
                candidate_id=identities["alternative"],
                evidence_references=(identities["claim"],),
                local=0,
                correlations=2,
                eligible=False,
            )
        ],
    )
    language = _proposal(
        text, "language", [], reason="no_eligible_action", outcome="defer"
    )

    def reader(_root: Path, _database: Path) -> a6.A6Probe:
        return a6.A6Probe(V22, frozenset(SCALAR), True, {case_id: (title, language)}, 0)

    return reader


def _breakdown_run(tmp_path: Path, *extra: str) -> tuple[int, str, str]:
    text = Sentinels()
    fixture, identities = _breakdown_fixture(text)
    database = _database(tmp_path / "a6.sqlite3", "v26-like", [fixture])
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ledger.main(
        [
            *_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
            "--a6-abstention",
            *extra,
        ],
        stdout=stdout,
        stderr=stderr,
        ancestry=lambda _commit, _revision: True,
        attention=lambda _root, _db: ledger.AttentionProbe((), 0, 0, SCALAR, True),
        decision_replay=_replay(text, database, identities),
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_the_breakdown_keeps_the_proposable_population_apart(tmp_path: Path) -> None:
    code, stdout, _ = _breakdown_run(tmp_path)
    assert code == 0
    section = json.loads(stdout)["a6_abstention"]
    entry = section["snapshots"][0]
    populations = {
        (row["population"], row["destination"], row["alternatives"]): row["fields"]
        for row in entry["populations"]
    }
    assert populations == {
        ("blocked", "language", "none"): 1,
        ("proposable", "title", "one"): 1,
    }
    assert entry["echo"] == [{"destination": "authors", "medium": "ebook", "fields": 1}]
    assert entry["replay_disagreements"] == 0
    checks = {
        (row["population"], row["check"]): row["candidates"]
        for row in entry["sub_checks"]
    }
    assert checks == {
        ("blocked", "no_candidate"): 1,
        ("proposable", "support_local"): 1,
    }
    (relaxation,) = entry["relaxations"]
    assert relaxation["rule"] == "title_two_correlations_without_local"
    assert relaxation["outcomes"]["admitted"] == 1
    assert (relaxation["true_positive"], relaxation["false_positive"]) == (1, 0)
    assert relaxation["field_classes"]["discriminable"] == 1
    assert relaxation["pick_agrees"] == 1
    pooled = {row["rule"]: row for row in section["pooled_relaxations"]}
    # n = 0 is not estimable, never a precision.
    assert pooled["authors_one_correlation"]["n"] == 0
    assert pooled["authors_one_correlation"]["precision"] is None
    assert pooled["authors_one_correlation"]["wilson_lower_bound"] is None


def test_the_breakdown_markdown_reads_not_estimable_at_n_zero(tmp_path: Path) -> None:
    code, markdown, _ = _breakdown_run(tmp_path, "--format", "markdown")
    assert code == 0
    # The breakdown's tables only: its page is one run's output.
    assert markdown.startswith("### Replay\n")
    assert "### Ranked gates" not in markdown
    assert (
        "| authors_one_correlation | pooled | both | 0 | 0 | 0 / 0 / 0 / 0 | 0 | 0/0/0 "
        "| not estimable |"
    ) in markdown
    assert (
        "| snap-a | proposable | title | insufficient_support | ebook | 1 (0 / 1 / 0) |"
        in (markdown)
    )


def test_the_breakdown_is_aggregate_only(tmp_path: Path) -> None:
    code, stdout, stderr = _breakdown_run(tmp_path)
    assert code == 0
    assert _SENTINEL not in stdout and _SENTINEL not in stderr
    keys: list[str] = []
    values: list[str] = []
    _strings(json.loads(stdout), keys, values)
    vocabulary = _test_vocabulary(["snap-a"]) | a6.VOCABULARY | {V22}
    vocabulary |= ledger._STRUCTURAL_KEYS
    vocabulary |= {"rich_refusals_collapsed"}
    for value in [*keys, *values]:
        assert value in vocabulary or len(value) == 40, value
    (tmp_path / "m").mkdir()
    markdown = _breakdown_run(tmp_path / "m", "--format", "markdown")[1]
    assert _SENTINEL not in markdown


def test_a_replay_that_disagrees_with_the_source_probe_refuses(tmp_path: Path) -> None:
    text = Sentinels()
    fixture, identities = _breakdown_fixture(text)
    database = _database(tmp_path / "a6.sqlite3", "v26-like", [fixture])
    code, stdout, stderr = _run(
        [
            *_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
            "--a6-abstention",
        ],
        attention=lambda _root, _db: ledger.AttentionProbe((), 0, 0, ("title",), True),
    )
    del identities
    assert (code, stdout) == (2, "")
    assert stderr.startswith("admission-rule-ledger: ")


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        json.dumps({"engine": {"version": "x", "scalar": [], "rich_split": True}}),
        json.dumps(
            {
                "engine": {"version": V22, "scalar": [_SENTINEL], "rich_split": True},
                "cases": {},
                "failed": 0,
            }
        ),
    ],
    ids=["unparsable", "missing-cases", "unknown-destination"],
)
def test_a_surprising_replay_document_is_a_fixed_refusal(document: str) -> None:
    with pytest.raises(ledger.Refusal) as refused:
        a6.parse_probe(document)
    assert _SENTINEL not in refused.value.message


def test_the_child_replays_a_product_case_and_the_mirror_agrees(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    report = _bibliographic_report(
        current_title="Vexmorin Ledger",
        providers=(
            _bibliographic_source(
                "catalogue", "edition-1", titles=("Vexmorin Ledger Restored",)
            ),
            _bibliographic_source(
                "registry", "edition-2", titles=("Vexmorin Ledger Restored",)
            ),
        ),
    )
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    repository.close()
    probe = a6.child_a6(REPOSITORY, database)
    assert probe.version == shadow_decision.DECISION_POLICY_VERSION
    assert probe.scalar == shadow_decision._SUPPORTED_DESTINATIONS
    # Main keeps rich refusals' own reasons, so A6b is separable on it.
    assert probe.rich_split
    assert probe.failed == 0 and len(probe.cases) == 1
    (proposals,) = probe.cases.values()
    assert proposals and {item.destination for item in proposals} <= probe.scalar
    for item in proposals:
        assert item.reason is not None
        _states, _compared, disagreed = a6.attribute(item, a6.table_for(probe.version))
        assert disagreed == 0
