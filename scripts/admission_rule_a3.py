"""The A3 bibliographic-combination analysis of the admission-rule ledger (#1746).

Row A3 counts the title, subtitle and series fields the proposal set leaves
unresolved because no coherent combination of the three survived
(``no_safe_bibliographic_combination``). The product overwrites each such
field: its earlier outcome, selection and reasons are replaced by the A3
reason alone. So the row mixes fields only A3 holds with fields another rule
held first, and with kept values A3 turned into a choice.

For each snapshot a child ``python -I`` process imports only the snapshot's
own source tree and, for every current case:

- rebuilds the report and replays that source's ``evaluate_report``, and
  compares the replayed results and relationships with the stored set;
- records every combination the source tried, with the facts its duplicate
  check (``_duplicates_bibliographic_component``) reads and its own verdict;
- on a case with A3 fields, replays again with ``_bibliographic_unresolved``
  bound to the identity function, which yields each field's outcome before
  the overwrite. Any difference outside the A3 fields refuses the analysis.

The parent then, in memory:

- classes every A3 field by its outcome before the overwrite: a value change
  only A3 holds, a kept value only A3 holds, or a field another rule held
  first (with that rule's reason);
- names the first containment clause that vetoes each tried combination,
  mirrored clause by clause and checked against the source's own verdict;
- re-checks each work under main's same-name series rule (#1099);
- evaluates three closed relaxations over the combination, never a single
  field, and screens what they would admit.

Everything leaves this module as counts in the ledger's closed vocabulary;
values and identifiers stay in memory.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from mechanical_librarian.domain import shadow_decision

# Direct execution puts scripts/ on sys.path and the ledger aliases itself
# there before importing this module; the test import resolves the scripts
# namespace package.
try:
    from scripts import admission_rule_ledger as ledger
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import admission_rule_ledger as ledger  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

KEY = "a3_combination"
A3_REASON = "no_safe_bibliographic_combination"
ATOMICITY_REASON = "bibliographic_combination_unresolved"
BIBLIOGRAPHIC = ("title", "subtitle", "series")
SAME_NAME_RULE = "79dbbf4c3eb58365e266655c32a0f6de3c784e51"  # #1099
WITHHELD_TITLE_RULE = "ce3a172d7aff06feacd604e98770422fb39a67a8"  # #1497
CANDIDATE = "candidate"
PRESERVE = "preserve_target"
_EMPTY_STATES = frozenset({"missing", "null", "empty"})
_OWN_CHANGE = frozenset({"propose_change", "acceptable_choice"})
_KEEP = "keep"
_FILLS = "fills_empty"
_REPLACES = "replaces_target"
_PRESERVES = "preserves_target"
_COMPETING = "competing_evidence"
_POLICY = re.compile(r"^fixed-conservative-v([1-9][0-9]{0,2})$")

# Containment clauses of the duplicate check, in attribution order. The
# series name with its number can fire only where the bare name fires too,
# so it is counted with the number.
CLAUSES = (
    "subtitle_in_title",
    "series_name_equals_title",
    "series_name_in_title",
    "series_number_in_title",
)
CAUSES = (
    *CLAUSES,
    "ambiguous_source",
    "too_many_combinations",
    "not_explained",
)
CLASSES = ("value_change", "kept_value", "held_first")
REPLAY_STATES = ("equal", "differs")
COUNTERFACTUAL_STATES = ("equal", "differs", "failed")
R1 = "series_fill_beside_kept_title_containing_name"
R2 = "same_name_series_beside_new_title"
R3 = "kept_companion_stays_kept"
RULES = (R1, R2, R3)
OUTCOMES = (
    "admitted",
    "main_forms",
    "refused_shape",
    "refused_masked",
    "refused_populated",
    "refused_clause",
    "refused_ambiguous",
    "refused_degraded",
    "refused_negative",
)
FIELD_CLASSES = ("tied", "discriminable", "no_right_work")


# ---------------------------------------------------------------------------
# The replay, as the child reports it.


@dataclass(frozen=True, slots=True)
class SeriesItem:
    name: tuple[str, ...]
    sequence: tuple[str, ...] | None
    name_sequence: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class Component:
    kind: str
    alternative: str | None
    state: str
    texts: tuple[tuple[str, ...], ...]
    items: tuple[SeriesItem, ...]


@dataclass(frozen=True, slots=True)
class Combination:
    """One combination the source tried, with its own duplicate verdict."""

    title_kind: str
    title_alternative: str | None
    title_tokens: tuple[str, ...] | None
    title_value: str | None
    title_kept: bool
    subtitle: Component
    series: Component
    verdict: bool

    def key(self) -> tuple[tuple[str, str | None], ...]:
        return (
            (self.title_kind, self.title_alternative),
            (self.subtitle.kind, self.subtitle.alternative),
            (self.series.kind, self.series.alternative),
        )


@dataclass(frozen=True, slots=True)
class Prior:
    """One bibliographic field's outcome before the A3 overwrite."""

    outcome: str
    reasons: tuple[str, ...]
    selected: str | None
    assessment: str | None
    alternatives: int
    all_preserve: bool


@dataclass(frozen=True, slots=True)
class CaseReplay:
    replay: str
    a3: tuple[str, ...]
    combinations: tuple[Combination, ...]
    counterfactual: str | None
    prior: Mapping[str, Prior]


@dataclass(frozen=True, slots=True)
class A3Probe:
    policy: str
    same_name_rule: bool
    limit: int
    cases: Mapping[str, CaseReplay]
    failed: int


A3Reader = Callable[[Path, Path], A3Probe]

_A3_CHILD = r"""
import inspect, json, os, sqlite3, sys
from urllib.parse import quote
root = os.path.realpath(sys.argv[1])
sys.path[:0] = [os.path.join(root, "src"), root]
import mechanical_librarian
from mechanical_librarian.domain import proposal_set as P
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.metadata_report import TargetDestination as T
for module in (mechanical_librarian, P):
    if not os.path.realpath(module.__file__).startswith(root + os.sep):
        raise SystemExit(3)
A3 = "no_safe_bibliographic_combination"
BIB = ("title", "subtitle", "series")
original_veto = P._duplicates_bibliographic_component
original_unresolved = P._bibliographic_unresolved
same_name = "title_kept" in inspect.signature(original_veto).parameters
tried = []
def tokens(value):
    return list(P._bibliographic_tokens(value))
def component(selection, targets, alternatives):
    value = P._selected_bibliographic_value(selection, targets, alternatives)
    texts, items = [], []
    if type(value) is str:
        texts.append(tokens(value))
    elif type(value) is tuple:
        for item in value:
            name, sequence = item.name, item.sequence
            items.append({
                "name": tokens(name),
                "sequence": None if sequence is None else tokens(sequence),
                "name_sequence": None if sequence is None
                else tokens(f"{name} {sequence}")})
    return {"kind": selection.kind.value, "alternative": selection.alternative_id,
            "state": targets[selection.destination].state.value,
            "texts": texts, "items": items}
def veto(selections, targets, results, *rest):
    verdict = bool(original_veto(selections, targets, results, *rest))
    title = results[T.TITLE]
    kept = bool(rest[0]) if rest else (
        title.outcome is P.ProposalOutcome.KEEP
        or tuple(title.reasons) == ("policy_ignored",))
    alternatives = {result.destination: {
        value.alternative_id: value for value in result.alternatives}
        for result in results.values()}
    selected = {value.destination: value for value in selections}
    chosen = selected[T.TITLE]
    value = P._selected_bibliographic_value(chosen, targets, alternatives)
    text = value if type(value) is str else None
    tried.append({
        "title_kind": chosen.kind.value, "title_alternative": chosen.alternative_id,
        "title_tokens": None if text is None else tokens(text), "title_value": text,
        "title_kept": kept,
        "subtitle": component(selected[T.SUBTITLE], targets, alternatives),
        "series": component(selected[T.SERIES], targets, alternatives),
        "verdict": verdict})
    return verdict
P._duplicates_bibliographic_component = veto
def prior(result):
    alternatives = result["alternatives"]
    selected = result.get("selected_alternative_id")
    chosen = next((value for value in alternatives
                   if value["alternative_id"] == selected), None)
    return {"outcome": result["outcome"], "reasons": list(result["reasons"]),
            "selected": selected,
            "assessment": None if chosen is None else chosen["assessment"],
            "alternatives": len(alternatives),
            "all_preserve": bool(alternatives) and all(
                value["assessment"] == "preserves_target" for value in alternatives)}
path = quote(os.path.abspath(sys.argv[2]), safe="/")
connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
connection.execute("PRAGMA query_only=ON")
statement = (
    "SELECT rc.review_case_id,ps.document,mr.document "
    "FROM review_cases rc JOIN proposal_sets ps "
    "ON ps.proposal_set_id=json_extract(rc.document,'$.proposal_set_id') "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
    "WHERE rc.review_case_id IN (SELECT review_case_id FROM current_review_packages "
    "WHERE review_case_id IS NOT NULL) ORDER BY rc.review_case_id")
cases = {}
failed = 0
for case_id, proposal_text, report_text in connection.execute(statement):
    try:
        stored = json.loads(proposal_text)
        report = MetadataReport.from_json(report_text)
        policy = stored.get("resolved_policy_id")
        del tried[:]
        replayed = json.loads(P.evaluate_report(report, policy).to_json())
        combinations = list(tried)
    except Exception:
        failed += 1
        continue
    equal = all(replayed.get(key) == stored.get(key)
                for key in ("results", "relationships"))
    by_destination = {value["destination"]: value for value in stored["results"]}
    a3 = [destination for destination in BIB
          if A3 in by_destination.get(destination, {}).get("reasons", [])]
    counterfactual, priors = None, {}
    if a3:
        P._bibliographic_unresolved = lambda result: result
        try:
            freed = json.loads(P.evaluate_report(report, policy).to_json())
        except Exception:
            freed = None
        finally:
            P._bibliographic_unresolved = original_unresolved
            del tried[:]
        if freed is None:
            counterfactual = "failed"
        else:
            unbound = {value["destination"]: value for value in freed["results"]}
            outside = any(
                unbound.get(destination) != result
                for destination, result in by_destination.items()
                if destination not in a3
            ) or set(unbound) != set(by_destination) or (
                freed.get("relationships") != stored.get("relationships"))
            counterfactual = "differs" if outside else "equal"
            priors = {destination: prior(unbound[destination])
                      for destination in BIB if destination in unbound}
    cases[case_id] = {"replay": "equal" if equal else "differs", "a3": a3,
                      "combinations": combinations,
                      "counterfactual": counterfactual, "prior": priors}
sys.stdout.write(json.dumps({
    "engine": {"policy": P.POLICY_VERSION, "same_name_rule": same_name,
               "limit": P.MAX_BIBLIOGRAPHIC_COMBINATIONS},
    "cases": cases, "failed": failed}))
"""


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError
    return value


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError
    return value


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _tokens(value: object) -> tuple[str, ...]:
    return tuple(_str(item) for item in cast("list[object]", value))


def _optional_tokens(value: object) -> tuple[str, ...] | None:
    return None if value is None else _tokens(value)


def _component(data: Mapping[str, object]) -> Component:
    return Component(
        kind=_str(data["kind"]),
        alternative=_optional_str(data["alternative"]),
        state=_str(data["state"]),
        texts=tuple(_tokens(item) for item in cast("list[object]", data["texts"])),
        items=tuple(
            SeriesItem(
                _tokens(item["name"]),
                _optional_tokens(item["sequence"]),
                _optional_tokens(item["name_sequence"]),
            )
            for item in cast("list[dict[str, object]]", data["items"])
        ),
    )


def _combination(data: Mapping[str, object]) -> Combination:
    return Combination(
        title_kind=_str(data["title_kind"]),
        title_alternative=_optional_str(data["title_alternative"]),
        title_tokens=_optional_tokens(data["title_tokens"]),
        title_value=_optional_str(data["title_value"]),
        title_kept=_bool(data["title_kept"]),
        subtitle=_component(cast("dict[str, object]", data["subtitle"])),
        series=_component(cast("dict[str, object]", data["series"])),
        verdict=_bool(data["verdict"]),
    )


def _prior(data: Mapping[str, object]) -> Prior:
    return Prior(
        outcome=_str(data["outcome"]),
        reasons=_tokens(data["reasons"]),
        selected=_optional_str(data["selected"]),
        assessment=_optional_str(data["assessment"]),
        alternatives=_int(data["alternatives"]),
        all_preserve=_bool(data["all_preserve"]),
    )


def _case(data: Mapping[str, object]) -> CaseReplay:
    replay = _str(data["replay"])
    counterfactual = _optional_str(data["counterfactual"])
    a3 = _tokens(data["a3"])
    if (
        replay not in REPLAY_STATES
        or (counterfactual is not None and counterfactual not in COUNTERFACTUAL_STATES)
        or not set(a3) <= set(BIBLIOGRAPHIC)
    ):
        raise ValueError
    return CaseReplay(
        replay=replay,
        a3=a3,
        combinations=tuple(
            _combination(item)
            for item in cast("list[dict[str, object]]", data["combinations"])
        ),
        counterfactual=counterfactual,
        prior={
            _str(destination): _prior(cast("dict[str, object]", item))
            for destination, item in cast("dict[object, object]", data["prior"]).items()
        },
    )


def parse_probe(text: str) -> A3Probe:
    """Validate the child's document; any surprise is a fixed refusal."""
    try:
        loaded = cast("dict[str, object]", json.loads(text))
        engine = cast("dict[str, object]", loaded["engine"])
        probe = A3Probe(
            policy=_str(engine["policy"]),
            same_name_rule=_bool(engine["same_name_rule"]),
            limit=_int(engine["limit"]),
            cases={
                _str(case_id): _case(cast("dict[str, object]", item))
                for case_id, item in cast(
                    "dict[object, object]", loaded["cases"]
                ).items()
            },
            failed=_int(loaded["failed"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ledger.Refusal("combination replay failed") from None
    if not _POLICY.match(probe.policy) or any(
        not set(case.prior) <= set(BIBLIOGRAPHIC) for case in probe.cases.values()
    ):
        raise ledger.Refusal("combination replay returned an unknown vocabulary")
    return probe


def child_a3(source_root: Path, database: Path) -> A3Probe:
    """Replay every current case's proposal set under its source in a child."""
    if not (source_root / "src" / "mechanical_librarian").is_dir():
        raise ledger.Refusal("source root is not an exported source tree")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _A3_CHILD,
                str(source_root),
                str(database),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=7200,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ledger.Refusal("combination replay failed") from None
    if completed.returncode != 0:
        raise ledger.Refusal("combination replay failed")
    return parse_probe(completed.stdout)


# ---------------------------------------------------------------------------
# The mirrored duplicate check.


def _contains(title: tuple[str, ...], component: tuple[str, ...]) -> bool:
    return bool(component) and any(
        title[index : index + len(component)] == component
        for index in range(len(title) - len(component) + 1)
    )


def _same_name(
    combination: Combination, item: SeriesItem, title: tuple[str, ...]
) -> bool:
    """Main's same-name series exception (#1099) for one series member."""
    return (
        combination.title_kind == PRESERVE
        and combination.title_kept
        and combination.series.kind == CANDIDATE
        and combination.series.state in _EMPTY_STATES
        and item.name == title
    )


def clauses(combination: Combination, *, same_name_rule: bool) -> tuple[str, ...]:
    """Every containment clause that fires on the combination, in order.

    With ``same_name_rule`` the series name equal to a kept title is allowed
    beside an empty series (#1099), as on main and on any source that has it.
    The combination is vetoed exactly when the tuple is not empty.
    """
    title = combination.title_tokens
    if not title:
        return ()
    fired: set[str] = set()
    for destination, part in (
        ("subtitle", combination.subtitle),
        ("series", combination.series),
    ):
        if combination.title_kind == PRESERVE and part.kind == PRESERVE:
            continue
        for text in part.texts:
            if _contains(title, text):
                fired.add(
                    "subtitle_in_title"
                    if destination == "subtitle"
                    else "series_name_in_title"
                )
        if destination != "series":
            continue
        for item in part.items:
            excepted = same_name_rule and _same_name(combination, item, title)
            if not excepted and _contains(title, item.name):
                fired.add(
                    "series_name_equals_title"
                    if item.name == title
                    else "series_name_in_title"
                )
            if (item.sequence is not None and _contains(title, item.sequence)) or (
                item.name_sequence is not None and _contains(title, item.name_sequence)
            ):
                fired.add("series_number_in_title")
    return tuple(clause for clause in CLAUSES if clause in fired)


def classify(prior: Prior | None) -> str:
    """A field's class from its outcome before the overwrite, never after it."""
    if prior is None:
        return "held_first"
    if prior.outcome in _OWN_CHANGE:
        return "value_change"
    if prior.outcome == _KEEP:
        return "kept_value"
    return "held_first"


def first_clauses(case: CaseReplay, *, same_name_rule: bool) -> tuple[str, ...]:
    """The first firing clause of each tried combination, as a set in order."""
    found = {
        fired[0]
        for item in case.combinations
        if (fired := clauses(item, same_name_rule=same_name_rule))
    }
    return tuple(clause for clause in CLAUSES if clause in found)


def work_causes(
    case: CaseReplay, limit: int, *, same_name_rule: bool
) -> tuple[str, ...]:
    """Why no combination survived for one work.

    Too many surviving combinations, or none tried because every source
    representation was ambiguous, is a work-level cause. Otherwise each
    tried combination names its first firing clause under the source's own
    rule, and the work counts under each of them.
    """
    survivors = {item.key() for item in case.combinations if not item.verdict}
    if len(survivors) > limit:
        return ("too_many_combinations",)
    if not case.combinations:
        return ("ambiguous_source",)
    if survivors:
        return ("not_explained",)
    return first_clauses(case, same_name_rule=same_name_rule) or ("not_explained",)


def stale_for_main(case: CaseReplay) -> bool:
    """Whether a tried combination survives main's veto (#1099)."""
    return any(not clauses(item, same_name_rule=True) for item in case.combinations)


# ---------------------------------------------------------------------------
# Closed relaxations, each a predicate over the combination (#1746 §6.5).


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: str
    # The fields whose values the relaxation would admit.
    admitted: tuple[str, ...] = ()
    combination: Combination | None = None


def _members(combination: Combination) -> tuple[tuple[str, str], ...]:
    return (
        ("title", combination.title_kind),
        ("subtitle", combination.subtitle.kind),
        ("series", combination.series.kind),
    )


def masked_member(combination: Combination, replay: CaseReplay) -> bool:
    """Whether a field the combination changes was held first by another rule."""
    return any(
        kind == CANDIDATE and classify(replay.prior.get(destination)) == "held_first"
        for destination, kind in _members(combination)
    )


def degraded_title(value: str | None) -> bool:
    """The checkout's degraded-title refusal (#1283) on the combination's title."""
    return (
        value is not None
        and shadow_decision._degraded_candidate_reason(
            {"destination": "title", "current": None}, {"value": value}
        )
        is not None
    )


def _alternative(
    case: ledger.Case, destination: str, identity: str | None
) -> dict[str, object]:
    for item in ledger._dicts(case.results.get(destination, {}).get("alternatives")):
        if identity is not None and item.get("alternative_id") == identity:
            return item
    return {}


def negative_record(case: ledger.Case, destination: str, identity: str | None) -> bool:
    """Whether the admitted alternative cites a record bound to another work."""
    alternative = _alternative(case, destination, identity)
    return any(
        case.labels.get(record) == "negative"
        for record in case.resolve(ledger._list(alternative.get("evidence_references")))
    )


def _refuse_shape(*priors: Prior | None) -> Verdict:
    held = any(classify(prior) == "held_first" for prior in priors)
    return Verdict("refused_masked" if held else "refused_shape")


def _qualify(
    shape: Sequence[Combination], replay: CaseReplay, clause: str
) -> Verdict | Combination:
    """The one combination of the shape whose only firing clause is ``clause``."""
    if not shape:
        return Verdict("refused_shape")
    if any(item.series.state not in _EMPTY_STATES for item in shape):
        return Verdict("refused_populated")
    open_shape = [item for item in shape if not masked_member(item, replay)]
    if not open_shape:
        return Verdict("refused_masked")
    qualifying = {
        item.key(): item
        for item in open_shape
        if clauses(item, same_name_rule=True) == (clause,)
    }
    if not qualifying:
        return Verdict("refused_clause")
    if len(qualifying) > 1:
        return Verdict("refused_ambiguous")
    (combination,) = qualifying.values()
    if degraded_title(combination.title_value):
        return Verdict("refused_degraded")
    return combination


def relax_r1(replay: CaseReplay, case: ledger.Case) -> Verdict:
    """R1: a series fill beside a kept title that already contains the name."""
    if stale_for_main(replay):
        return Verdict("main_forms")
    title, series = replay.prior.get("title"), replay.prior.get("series")
    if (
        title is None
        or series is None
        or title.outcome != _KEEP
        or series.outcome != "propose_change"
    ):
        return _refuse_shape(title, series)
    if series.assessment != _FILLS or series.alternatives != 1:
        return Verdict("refused_shape")
    shape = [
        item
        for item in replay.combinations
        if item.title_kind == PRESERVE
        and item.series.kind == CANDIDATE
        and item.series.alternative == series.selected
    ]
    found = _qualify(shape, replay, "series_name_in_title")
    if isinstance(found, Verdict):
        return found
    if negative_record(case, "series", series.selected):
        return Verdict("refused_negative")
    return Verdict("admitted", ("series",), found)


def relax_r2(replay: CaseReplay, case: ledger.Case) -> Verdict:
    """R2: a series named exactly like a new title, beside an empty series."""
    del case
    if stale_for_main(replay):
        return Verdict("main_forms")
    title, series = replay.prior.get("title"), replay.prior.get("series")
    if (
        title is None
        or series is None
        or title.outcome != "propose_change"
        or series.outcome not in _OWN_CHANGE
    ):
        return _refuse_shape(title, series)
    shape = [
        item
        for item in replay.combinations
        if item.title_kind == CANDIDATE
        and item.title_alternative == title.selected
        and item.series.kind == CANDIDATE
        and item.series.alternative == series.selected
    ]
    found = _qualify(shape, replay, "series_name_equals_title")
    if isinstance(found, Verdict):
        return found
    return Verdict("admitted", ("title", "series"), found)


def relax_r3(replay: CaseReplay, destination: str) -> Verdict:
    """R3: a kept field whose every alternative preserves it stays kept.

    It admits no value: it would only end that field's own attention.
    """
    if stale_for_main(replay):
        return Verdict("main_forms")
    prior = replay.prior.get(destination)
    if prior is None or prior.outcome != _KEEP or not prior.all_preserve:
        return Verdict("refused_shape")
    return Verdict("admitted")


# ---------------------------------------------------------------------------
# Screens for an admitted field.


def _normal(value: object) -> str | None:
    return " ".join(value.casefold().split()) if isinstance(value, str) else None


def _admitted_values(alternative: Mapping[str, object], destination: str) -> set[str]:
    value = ledger._dict(alternative.get("value")).get("value")
    if destination == "series":
        return {
            name
            for item in ledger._dicts(value)
            if (name := _normal(item.get("name"))) is not None
        }
    normal = _normal(value)
    return set() if normal is None else {normal}


def value_agreement(
    case: ledger.Case, destination: str, alternative: Mapping[str, object]
) -> bool | None:
    """Whether an identifier-accepted record the field does not cite agrees.

    None when no such record states a value for the field.
    """
    cited = case.resolve(ledger._list(alternative.get("evidence_references")))
    accepted = {
        identity
        for evidence in case.identifier_evidence()
        if evidence.get("relation") == "accepted"
        and isinstance(identity := evidence.get("candidate_identity"), str)
        and identity in case.records
        and identity not in cited
    }
    admitted = _admitted_values(alternative, destination)
    stated: list[str] = []
    for provider in case.providers():
        for candidate in ledger._dicts(provider.get("candidates")):
            if candidate.get("identity") not in accepted:
                continue
            stated += [
                normal
                for claim in ledger._dicts(candidate.get("claims"))
                if claim.get("kind") == destination
                and (normal := _normal(claim.get("value"))) is not None
            ]
    if not stated:
        return None
    return any(value in admitted for value in stated)


def field_class(
    case: ledger.Case, destination: str, identity: str | None
) -> tuple[str, bool]:
    """AO's class of the field and whether the admitted alternative is the right one."""
    alternatives = ledger._dicts(case.results.get(destination, {}).get("alternatives"))
    right = [item for item in alternatives if ledger._right_work(case, item)]
    if len(right) > 1:
        return "tied", False
    if len(right) == 1:
        return "discriminable", right[0].get("alternative_id") == identity
    return "no_right_work", False


# ---------------------------------------------------------------------------
# Measurement.


@dataclass(slots=True)
class RelaxationTally:
    outcomes: Counter[str] = field(default_factory=Counter)
    fields: int = 0
    cases: int = 0
    true_positive: int = 0
    false_positive: int = 0
    unlabelled: int = 0
    unreached: int = 0
    classes: Counter[str] = field(default_factory=Counter)
    agrees: int = 0
    preserves_current: int = 0
    agreement: int = 0
    agreement_n: int = 0
    containment: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class A3Tally:
    replayed: int = 0
    replay_differs: int = 0
    counterfactual_differs: int = 0
    counterfactual_failed: int = 0
    combinations_tried: int = 0
    mirror_disagreements: int = 0
    works: int = 0
    blocked: Counter[str] = field(default_factory=Counter)
    stale_fields: Counter[str] = field(default_factory=Counter)
    stale_works: int = 0
    remaining: Counter[tuple[str, str]] = field(default_factory=Counter)
    fills: Counter[str] = field(default_factory=Counter)
    replacements: Counter[str] = field(default_factory=Counter)
    held_first_election: Counter[str] = field(default_factory=Counter)
    held_first_reasons: Counter[tuple[str, str]] = field(default_factory=Counter)
    atomicity: Counter[str] = field(default_factory=Counter)
    cause_works: Counter[str] = field(default_factory=Counter)
    cause_fields: Counter[str] = field(default_factory=Counter)
    cause_combinations: Counter[str] = field(default_factory=Counter)
    relaxations: dict[str, RelaxationTally] = field(
        default_factory=lambda: {rule: RelaxationTally() for rule in RULES}
    )


_CASES_SQL = (
    "SELECT rc.review_case_id,rc.document,mr.document,"
    "(SELECT shadow.document FROM shadow_decision_outcomes shadow "
    "WHERE shadow.review_case_id=rc.review_case_id "
    "ORDER BY shadow.evaluated_at DESC LIMIT 1),"
    "(SELECT consideration.document FROM current_cover_considerations current_cover "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=current_cover.consideration_id "
    "WHERE current_cover.review_case_id=rc.review_case_id LIMIT 1) "
    "FROM review_cases rc JOIN metadata_reports mr ON mr.report_id=rc.report_id "
    "WHERE rc.review_case_id IN (SELECT review_case_id FROM current_review_packages "
    "WHERE review_case_id IS NOT NULL) ORDER BY rc.review_case_id"
)


def _screen(
    case: ledger.Case,
    destination: str,
    identity: str | None,
    tally: RelaxationTally,
) -> None:
    alternative = _alternative(case, destination, identity)
    records = case.resolve(ledger._list(alternative.get("evidence_references")))
    tally.fields += 1
    if not records:
        tally.unreached += 1
    for record in records:
        label = case.labels.get(record, "unlabelled")
        if label == "positive":
            tally.true_positive += 1
        elif label == "negative":
            tally.false_positive += 1
        else:
            tally.unlabelled += 1
    kind, agrees = field_class(case, destination, identity)
    tally.classes[kind] += 1
    tally.agrees += agrees
    tally.preserves_current += alternative.get("assessment") == _PRESERVES
    agreement = value_agreement(case, destination, alternative)
    if agreement is not None:
        tally.agreement_n += 1
        tally.agreement += agreement


def measure_a3(
    connection: sqlite3.Connection,
    probe: A3Probe,
    elections: frozenset[str] = frozenset(),
) -> A3Tally:
    """Stream the current cases once and tally the A3 analysis."""
    tally = A3Tally(replayed=len(probe.cases))
    for replayed in probe.cases.values():
        tally.replay_differs += replayed.replay != "equal"
        for item in replayed.combinations:
            tally.combinations_tried += 1
            tally.mirror_disagreements += item.verdict != bool(
                clauses(item, same_name_rule=probe.same_name_rule)
            )
    try:
        rows = connection.execute(_CASES_SQL)
        for case_id, case_text, report_text, shadow_text, consideration_text in rows:
            shadow = ledger._document(shadow_text)
            case = ledger._build_case(
                ledger._document(case_text),
                ledger._document(report_text),
                shadow,
                ledger._document(consideration_text),
            )
            for outcome in ledger._dicts(shadow.get("outcomes")):
                destination = ledger._text(outcome.get("destination"))
                if (
                    destination is not None
                    and outcome.get("reason_code") == ATOMICITY_REASON
                ):
                    tally.atomicity[destination] += 1
            a3 = [
                destination
                for destination in BIBLIOGRAPHIC
                if A3_REASON
                in ledger._list(case.results.get(destination, {}).get("reasons"))
                and case.blocked(destination)
            ]
            if a3:
                replay = probe.cases.get(case_id)
                if replay is None or replay.replay != "equal":
                    raise ledger.Refusal("combination replay differs on an A3 case")
                _measure_work(tally, case, replay, a3, probe, elections)
    except sqlite3.Error:
        raise ledger.Refusal("snapshot schema unrecognised") from None
    return tally


def _measure_work(
    tally: A3Tally,
    case: ledger.Case,
    replay: CaseReplay,
    a3: Sequence[str],
    probe: A3Probe,
    elections: frozenset[str],
) -> None:
    if replay.counterfactual == "failed":
        tally.counterfactual_failed += 1
    if replay.counterfactual != "equal" or tuple(a3) != replay.a3:
        tally.counterfactual_differs += replay.counterfactual == "differs"
        raise ledger.Refusal("the counterfactual changed a field outside A3")
    tally.works += 1
    for name in work_causes(replay, probe.limit, same_name_rule=probe.same_name_rule):
        tally.cause_works[name] += 1
        tally.cause_fields[name] += len(a3)
    for item in replay.combinations:
        fired = clauses(item, same_name_rule=probe.same_name_rule)
        if fired:
            tally.cause_combinations[fired[0]] += 1
    stale = stale_for_main(replay)
    tally.stale_works += stale
    for destination in a3:
        tally.blocked[destination] += 1
        if stale:
            tally.stale_fields[destination] += 1
            continue
        prior = replay.prior.get(destination)
        kind = classify(prior)
        tally.remaining[(destination, kind)] += 1
        if kind == "value_change" and prior is not None:
            if prior.assessment == _FILLS:
                tally.fills[destination] += 1
            elif prior.assessment == _REPLACES:
                tally.replacements[destination] += 1
        if kind == "held_first":
            reasons = () if prior is None else prior.reasons
            for reason in reasons or (ledger._OTHER_REASON,):
                known = reason if reason in ledger.REASONS else ledger._OTHER_REASON
                tally.held_first_reasons[(destination, known)] += 1
            if destination in elections and _COMPETING in reasons:
                tally.held_first_election[destination] += 1
    for rule, relax in ((R1, relax_r1), (R2, relax_r2)):
        verdict = relax(replay, case)
        result = tally.relaxations[rule]
        result.outcomes[verdict.outcome] += 1
        if verdict.combination is not None:
            for clause in clauses(verdict.combination, same_name_rule=True):
                result.containment[clause] += 1
        for destination in verdict.admitted:
            _screen(case, destination, replay.prior[destination].selected, result)
    kept = tally.relaxations[R3]
    verdicts = [relax_r3(replay, destination) for destination in a3]
    for verdict in verdicts:
        kept.outcomes[verdict.outcome] += 1
    if all(verdict.outcome == "admitted" for verdict in verdicts):
        kept.cases += 1


# ---------------------------------------------------------------------------
# Closed output.


def _relaxation_entry(tally: RelaxationTally) -> dict[str, object]:
    total = tally.true_positive + tally.false_positive
    lower = ledger.wilson_lower_bound(tally.true_positive, total)
    return {
        "outcomes": {name: tally.outcomes.get(name, 0) for name in OUTCOMES},
        "fields": tally.fields,
        "cases": tally.cases,
        "true_positive": tally.true_positive,
        "false_positive": tally.false_positive,
        "unlabelled": tally.unlabelled,
        "unreached_fields": tally.unreached,
        "n": total,
        "precision": round(tally.true_positive / total, 4) if total else None,
        "wilson_lower_bound": None if lower is None else round(lower, 4),
        "field_classes": {name: tally.classes.get(name, 0) for name in FIELD_CLASSES},
        "pick_agrees": tally.agrees,
        "preserves_current": tally.preserves_current,
        "value_agreement": tally.agreement,
        "value_agreement_n": tally.agreement_n,
        "containment": {name: tally.containment.get(name, 0) for name in CLAUSES},
    }


def _merge(tallies: Iterable[RelaxationTally]) -> RelaxationTally:
    merged = RelaxationTally()
    for tally in tallies:
        merged.outcomes.update(tally.outcomes)
        merged.containment.update(tally.containment)
        merged.classes.update(tally.classes)
        for name in (
            "fields",
            "cases",
            "true_positive",
            "false_positive",
            "unlabelled",
            "unreached",
            "agrees",
            "preserves_current",
            "agreement",
            "agreement_n",
        ):
            setattr(merged, name, getattr(merged, name) + getattr(tally, name))
    return merged


def snapshot_entry(
    label: str,
    probe: A3Probe,
    tally: A3Tally,
    *,
    withheld_title_rule: bool,
    human_decisions: int,
) -> dict[str, object]:
    populations = [
        {
            "destination": destination,
            "blocked": tally.blocked.get(destination, 0),
            "stale_later_rule": tally.stale_fields.get(destination, 0),
            "value_change": tally.remaining.get((destination, "value_change"), 0),
            "fills": tally.fills.get(destination, 0),
            "replacements": tally.replacements.get(destination, 0),
            "kept_value": tally.remaining.get((destination, "kept_value"), 0),
            "held_first": tally.remaining.get((destination, "held_first"), 0),
            "held_first_later_election": tally.held_first_election.get(destination, 0),
            "atomicity": tally.atomicity.get(destination, 0),
        }
        for destination in BIBLIOGRAPHIC
    ]
    return {
        "label": label,
        "proposal_policy": probe.policy,
        "same_name_rule_at_source": probe.same_name_rule,
        "withheld_title_rule_at_source": withheld_title_rule,
        "human_decisions": human_decisions,
        "replayed_cases": tally.replayed,
        "replay_differs": tally.replay_differs,
        "replay_failed_cases": probe.failed,
        "counterfactual_differs": tally.counterfactual_differs,
        "counterfactual_failed": tally.counterfactual_failed,
        "combinations_tried": tally.combinations_tried,
        "mirror_disagreements": tally.mirror_disagreements,
        "works": tally.works,
        "stale_works": tally.stale_works,
        "populations": populations,
        "held_first_reasons": [
            {"destination": destination, "reason": reason, "fields": count}
            for (destination, reason), count in sorted(
                tally.held_first_reasons.items(),
                key=lambda item: (BIBLIOGRAPHIC.index(item[0][0]), item[0][1]),
            )
        ],
        "causes": [
            {
                "cause": name,
                "works": tally.cause_works.get(name, 0),
                "fields": tally.cause_fields.get(name, 0),
                "combinations": tally.cause_combinations.get(name, 0),
            }
            for name in CAUSES
        ],
        "relaxations": [
            {"rule": rule, **_relaxation_entry(tally.relaxations[rule])}
            for rule in RULES
        ],
    }


def pooled_relaxations(tallies: Sequence[A3Tally]) -> list[dict[str, object]]:
    return [
        {
            "rule": rule,
            **_relaxation_entry(_merge(tally.relaxations[rule] for tally in tallies)),
        }
        for rule in RULES
    ]


def human_decisions(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("SELECT count(*) FROM human_decisions").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row is not None else 0


VOCABULARY = frozenset(
    {
        KEY,
        "proposal_policy",
        "same_name_rule_at_source",
        "withheld_title_rule_at_source",
        "human_decisions",
        "replayed_cases",
        "replay_differs",
        "replay_failed_cases",
        "counterfactual_differs",
        "counterfactual_failed",
        "combinations_tried",
        "mirror_disagreements",
        "stale_works",
        "populations",
        "destination",
        "blocked",
        "stale_later_rule",
        "fills",
        "replacements",
        "held_first_later_election",
        "atomicity",
        "held_first_reasons",
        "reason",
        "causes",
        "cause",
        "combinations",
        "relaxations",
        "rule",
        "outcomes",
        "field_classes",
        "preserves_current",
        "value_agreement",
        "value_agreement_n",
        "containment",
        "pooled_relaxations",
        *CLASSES,
        *CAUSES,
        *OUTCOMES,
        *FIELD_CLASSES,
        *RULES,
    }
)


def vocabulary(section: Mapping[str, object]) -> frozenset[str]:
    """The analysis's closed strings, plus each validated proposal policy."""
    policies = {
        cast("str", entry["proposal_policy"])
        for entry in ledger._dicts(section.get("snapshots"))
        if _POLICY.match(cast("str", entry["proposal_policy"]))
    }
    return VOCABULARY | policies


# ---------------------------------------------------------------------------
# Markdown.

CAUSE_LABELS = {
    "subtitle_in_title": "subtitle in title",
    "series_name_equals_title": "series name equals title",
    "series_name_in_title": "series name in title",
    "series_number_in_title": "series number in title",
    "ambiguous_source": "ambiguous source",
    "too_many_combinations": "too many combinations",
    "not_explained": "not explained",
}


def _n(value: object) -> str:
    return f"{cast('int', value):,}"


def _yes(value: object) -> str:
    return "yes" if value else "no"


def _ratio(entry: Mapping[str, object]) -> str:
    if entry["precision"] is None or entry["wilson_lower_bound"] is None:
        return "not estimable"
    return (
        f"{cast('float', entry['precision']):.3f} "
        f"({cast('float', entry['wilson_lower_bound']):.3f})"
    )


def _agreement(entry: Mapping[str, object]) -> str:
    total = cast("int", entry["value_agreement_n"])
    if not total:
        return "not estimable (0 comparable)"
    return f"{_n(entry['value_agreement'])}/{_n(total)}"


def _containment(entry: Mapping[str, object]) -> str:
    counts = cast("dict[str, int]", entry["containment"])
    found = [
        f"{CAUSE_LABELS[name]} {_n(counts[name])}" for name in CLAUSES if counts[name]
    ]
    return ", ".join(found) if found else "none"


def _relaxation_cells(entry: Mapping[str, object]) -> list[str]:
    outcomes = cast("dict[str, int]", entry["outcomes"])
    classes = cast("dict[str, int]", entry["field_classes"])
    refused = (
        "refused_shape",
        "refused_masked",
        "refused_populated",
        "refused_clause",
        "refused_ambiguous",
        "refused_degraded",
        "refused_negative",
    )
    return [
        _n(sum(outcomes.values())),
        _n(outcomes["main_forms"]),
        " / ".join(_n(outcomes[name]) for name in refused),
        _n(outcomes["admitted"]),
        _n(entry["fields"]) if entry["rule"] != R3 else "none: no value changes",
        _n(entry["cases"]) if entry["rule"] == R3 else "not applicable",
        f"{entry['true_positive']}/{entry['false_positive']}/{entry['unlabelled']}",
        _ratio(entry),
        f"{_n(classes['tied'])} / {_n(classes['discriminable'])} "
        f"({_n(entry['pick_agrees'])} right-work) / {_n(classes['no_right_work'])}",
        _n(entry["preserves_current"]),
        _agreement(entry),
        _containment(entry),
    ]


def render(section: Mapping[str, object]) -> list[str]:
    snapshots = ledger._dicts(section.get("snapshots"))
    lines = [
        "### Replay",
        "",
        "| Snapshot | Proposal policy | Same-name series rule (#1099) at source | "
        "Owner decisions | Cases replayed | Replayed results that differ | "
        "Cases not replayed | Combinations tried | "
        "Mirror disagreements with the source |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        lines.append(
            f"| {entry['label']} | {entry['proposal_policy']} | "
            f"{_yes(entry['same_name_rule_at_source'])} | "
            f"{_n(entry['human_decisions'])} | {_n(entry['replayed_cases'])} | "
            f"{_n(entry['replay_differs'])} | {_n(entry['replay_failed_cases'])} | "
            f"{_n(entry['combinations_tried'])} | "
            f"{_n(entry['mirror_disagreements'])} of {_n(entry['combinations_tried'])} |"
        )
    lines += [
        "",
        "### Populations",
        "",
        "| Snapshot | Field | Blocked | Stale: later rule (#1099) | "
        "Held only by this rule: value change (fill / replacement) | "
        "Held only by this rule: kept value | "
        "Held first by another rule (stale: later election) | "
        "Decided together, left unresolved (not scored) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        for row in ledger._dicts(entry["populations"]):
            lines.append(
                f"| {entry['label']} | {row['destination']} | {_n(row['blocked'])} | "
                f"{_n(row['stale_later_rule'])} | {_n(row['value_change'])} "
                f"({_n(row['fills'])} / {_n(row['replacements'])}) | "
                f"{_n(row['kept_value'])} | {_n(row['held_first'])} "
                f"({_n(row['held_first_later_election'])}) | {_n(row['atomicity'])} |"
            )
    lines += [
        "",
        "| Snapshot | Field | Reason it was held first | Fields |",
        "|---|---|---|---|",
    ]
    for entry in snapshots:
        for row in ledger._dicts(entry["held_first_reasons"]):
            lines.append(
                f"| {entry['label']} | {row['destination']} | {row['reason']} | "
                f"{_n(row['fields'])} |"
            )
    lines += [
        "",
        "### Why no combination survived",
        "",
        "| Snapshot | Cause | Works | Fields | Combinations |",
        "|---|---|---|---|---|",
    ]
    for entry in snapshots:
        for row in ledger._dicts(entry["causes"]):
            if row["cause"] == "not_explained" and not row["works"]:
                continue
            lines.append(
                f"| {entry['label']} | {CAUSE_LABELS[cast('str', row['cause'])]} | "
                f"{_n(row['works'])} | {_n(row['fields'])} | "
                f"{_n(row['combinations'])} |"
            )
    lines += [
        "",
        "### Stale for main",
        "",
        "| Snapshot | Works | Stale: later rule (#1099) | "
        "Works with no combination on main | Withheld-title rule (#1497) |",
        "|---|---|---|---|---|",
    ]
    for entry in snapshots:
        works, stale = cast("int", entry["works"]), cast("int", entry["stale_works"])
        lines.append(
            f"| {entry['label']} | {_n(works)} | {_n(stale)} | {_n(works - stale)} | "
            + (
                "present at source"
                if entry["withheld_title_rule_at_source"]
                else "not present at source"
            )
            + " |"
        )
    header = (
        "| Relaxation | Snapshot | Evaluated | Main already forms one | "
        "Refused: shape / held first / series populated / other clause / several / "
        "degraded title / wrong-work record | Admitted | Admitted fields | "
        "Cases with no held sibling | "
        "Cited records: right work / wrong work / unlabelled | "
        "Identity precision (Wilson 95% lower limit) | "
        "Fields: tied / decided (admitted is the right-work one) / no right-work | "
        "Repeats current value | Value agreement with an uncited record | "
        "Containment kept after admission |"
    )
    lines += ["", "### Relaxations", "", header, "|" + "---|" * 15]
    for entry in snapshots:
        for row in ledger._dicts(entry["relaxations"]):
            lines.append(
                f"| {row['rule']} | {entry['label']} | "
                + " | ".join(_relaxation_cells(row))
                + " |"
            )
    for row in ledger._dicts(section.get("pooled_relaxations")):
        lines.append(
            f"| {row['rule']} | pooled | " + " | ".join(_relaxation_cells(row)) + " |"
        )
    return lines
