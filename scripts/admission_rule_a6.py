"""The A6 abstention analysis of the admission-rule ledger (#1745).

Automatic-decision abstention is measured here on two populations, never on
the ledger's blocked fields alone:

- **blocked**: the proposal set already refused the field and the scalar
  decision engine abstained with ``insufficient_support`` or
  ``no_eligible_action`` (the ledger's A6a row);
- **proposable**: the proposal set proposes a change or an acceptable choice,
  the field's policy destination is automatic, and the decision still
  abstains with one of those two reasons. This is the abstention's own cost.

For each field a child ``python -I`` process rebuilds the decision packet with
the snapshot's own source revision (``ShadowReviewPacket.create`` over the
stored review case, proposal set and report), replays that revision's
decision, and reports the support counts and checks of every candidate. The
parent then:

- attributes each candidate to the first failing named check of the source
  engine's ``_candidate_is_eligible``, mirrored clause by clause and verified
  against the source's own verdict for every candidate;
- evaluates one closed relaxation per scalar destination under the current
  checkout's checks: the unique candidate that fails only one named support
  threshold, passes it relaxed by one step, and is not degraded. Ordering is
  never used;
- screens an admitted candidate by identity (the ledger's natural controls),
  by AO's field classes (tied, discriminable, no right-work alternative), by
  whether it only preserves the current value, and by value agreement with
  an identifier-accepted record the candidate does not cite.

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
from typing import Literal, cast

from mechanical_librarian.domain import shadow_decision

# Direct execution puts scripts/ on sys.path and the ledger aliases itself
# there before importing this module; the test import resolves the scripts
# namespace package.
try:
    from scripts import admission_rule_ledger as ledger
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import admission_rule_ledger as ledger  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

KEY = "a6_abstention"
A6_REASONS = ("insufficient_support", "no_eligible_action")
ECHO_REASON = "evidence_sufficiency_manual"
RICH_RESIDUAL_REASON = "no_eligible_action"
POPULATIONS = ("blocked", "proposable")
_PROPOSABLE_OUTCOMES = frozenset({"propose_change", "acceptable_choice"})
_AUTOMATIC = "automatic"
_ABSTENTION = "abstention"
ALTERNATIVE_CLASSES = ("none", "one", "more")

# Named checks of _candidate_is_eligible, in attribution order. From v23 the
# source refuses a degraded candidate (#1283) before any other check.
CHECKS = (
    "degraded",
    "scope",
    "binding",
    "citations",
    "publication",
    "preservation",
    "support_local",
    "support_correlation",
    "narrator",
    "evidence_led_title",
)
# Candidate and field states that are not a failing check.
STATES = (
    "passes",
    "no_action",
    "not_evaluated",
    "no_candidate",
    "language_conflict",
    "not_replayed",
    "not_mirrored",
)
_WORK = frozenset({"title", "authors"})
_PUBLICATION = "publishedDate"
_PRECISIONS = frozenset({"year", "month", "day"})
_ENGINE = re.compile(r"^deterministic-shadow-decision-policy-v([1-9][0-9]{0,2})$")
Table = Literal["v9", "v22", "v23"]
# v23 is v22 plus the degraded-candidate refusal; everything else is shared.
_V22_FAMILY: frozenset[str] = frozenset({"v22", "v23"})


@dataclass(frozen=True, slots=True)
class Relaxation:
    """One closed support-threshold relaxation (#1745 D2)."""

    rule: str
    destination: str
    check: str
    # The relaxed threshold, over (local evidence, independent correlations,
    # date precision).
    relaxed: Callable[[int, int, str | None], bool]


def _two_correlations_without_local(
    local: int, correlations: int, _p: str | None
) -> bool:
    return local == 0 and correlations >= 2


def _one_correlation(_local: int, correlations: int, _p: str | None) -> bool:
    return correlations >= 1


def _one_correlation_day(_local: int, correlations: int, precision: str | None) -> bool:
    return correlations >= 1 and precision == "day"


RELAXATIONS: tuple[Relaxation, ...] = (
    Relaxation(
        "authors_one_correlation",
        "authors",
        "support_correlation",
        _one_correlation,
    ),
    Relaxation(
        "title_two_correlations_without_local",
        "title",
        "support_local",
        _two_correlations_without_local,
    ),
    Relaxation(
        "publisher_two_correlations_without_local",
        "publisher",
        "support_local",
        _two_correlations_without_local,
    ),
    Relaxation(
        "language_two_correlations_without_local",
        "language",
        "support_local",
        _two_correlations_without_local,
    ),
    Relaxation(
        "published_date_day_one_correlation",
        "publishedDate",
        "support_correlation",
        _one_correlation_day,
    ),
)
RELAXATION_BY_DESTINATION = {item.destination: item for item in RELAXATIONS}
OUTCOMES = (
    "admitted",
    "refused_none",
    "refused_degraded",
    "refused_ambiguous",
    "refused_conflict",
    "refused_reason",
    "main_passes",
    "not_replayed",
)
FIELD_CLASSES = ("tied", "discriminable", "no_right_work")


# ---------------------------------------------------------------------------
# The replayed packet, as the child reports it.


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    value: object
    scope: str
    date_event: str | None
    date_precision: str | None
    assessment: str
    evidence_references: tuple[str, ...]
    evidence_scope: str
    binding: str
    recording: bool
    narrator_agreement: bool
    independent_title: bool
    publication_event: str | None
    publication_consistency: str | None
    has_publication: bool
    local: int
    correlations: int
    preservation: int
    action: bool
    citations_present: bool
    citations_known: bool
    combinations_known: bool
    # The source revision's own _candidate_is_eligible verdict; None without
    # a candidate action.
    eligible: bool | None


@dataclass(frozen=True, slots=True)
class Proposal:
    destination: str
    outcome: str
    reasons: tuple[str, ...]
    current: object
    sufficiency: str
    preservation: int
    report_only: bool
    consensus_title: bool
    tidy_title: bool
    evidence_led: bool
    reason: str | None
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class A6Probe:
    version: str
    scalar: frozenset[str]
    rich_split: bool
    cases: Mapping[str, tuple[Proposal, ...]]
    failed: int


A6Reader = Callable[[Path, Path], A6Probe]

_A6_CHILD = r"""
import json, os, sqlite3, sys
from urllib.parse import quote
root = os.path.realpath(sys.argv[1])
sys.path[:0] = [os.path.join(root, "src"), root]
import mechanical_librarian
from mechanical_librarian.domain import shadow_decision as D
from mechanical_librarian.domain import shadow_outcome as O
from mechanical_librarian.domain import proposal_set as P
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.shadow_review_actions import (
    compile_shadow_review_actions,
)
for module in (mechanical_librarian, D, O, P):
    if not os.path.realpath(module.__file__).startswith(root + os.sep):
        raise SystemExit(3)
engine = {"version": D.DECISION_POLICY_VERSION,
          "scalar": sorted(D._SUPPORTED_DESTINATIONS),
          "rich_split": hasattr(O, "_RICH_REFUSAL_REASONS")}
report_only = D._REPORT_ONLY_SOURCE_DESTINATIONS
consensus = getattr(P, "PROVIDER_CONSENSUS_TITLE_REASONS", frozenset())
tidy = getattr(P, "DECORATION_TIDY_TITLE_REASONS", frozenset())
led = getattr(P, "EVIDENCE_LED_TITLE_REASON", None)
path = quote(os.path.abspath(sys.argv[2]), safe="/")
connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
connection.execute("PRAGMA query_only=ON")
statement = (
    "SELECT rc.review_case_id,rc.document,ps.document,mr.document "
    "FROM review_cases rc JOIN proposal_sets ps "
    "ON ps.proposal_set_id=json_extract(rc.document,'$.proposal_set_id') "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
    "WHERE rc.review_case_id IN (SELECT review_case_id FROM current_review_packages "
    "WHERE review_case_id IS NOT NULL) ORDER BY rc.review_case_id")
cases = {}
failed = 0
for case_id, case_text, proposal_text, report_text in connection.execute(statement):
    try:
        packet = ShadowReviewPacket.create(
            ReviewCase.from_json(case_text),
            P.ProposalSet.from_json(proposal_text),
            MetadataReport.from_json(report_text))
        catalogue = compile_shadow_review_actions(packet)
        decided = {value.proposal_id: value.reason_code
                   for value in D.decide_shadow_packet(packet).decisions}
    except Exception:
        failed += 1
        continue
    citations = {value["citation_id"] for value in packet.citations}
    combinations = {value["combination_id"]
                    for value in packet.bibliographic_combinations}
    proposals = []
    for proposal in packet.proposals:
        destination = proposal["destination"]
        if destination not in D._SUPPORTED_DESTINATIONS:
            continue
        reasons = list(proposal["reasons"])
        rows = []
        for candidate in proposal["candidates"]:
            summary = candidate["decision_summary"]
            support = summary["support"]
            action = catalogue.action_for(
                proposal["proposal_id"], "recommend_candidate",
                candidate["candidate_id"])
            publication = summary.get("publication")
            publication = publication if type(publication) is dict else None
            rows.append({
                "candidate_id": candidate["candidate_id"],
                "value": candidate["value"],
                "scope": candidate["scope"],
                "date_event": candidate["date_event"],
                "date_precision": candidate["date_precision"],
                "assessment": candidate["assessment"],
                "evidence_references": list(candidate["evidence_references"]),
                "evidence_scope": summary["evidence_scope"],
                "binding": summary["selected_manifestation_binding"],
                "recording": bool(summary.get("recording_support", False)),
                "narrator_agreement": bool(summary.get("narrator_agreement", False)),
                "independent_title": bool(summary.get("independent_title", False)),
                "has_publication": publication is not None,
                "publication_event": None if publication is None
                else publication.get("event"),
                "publication_consistency": None if publication is None
                else publication.get("paired_destination_consistency"),
                "local": support["local_evidence"],
                "correlations": support["independent_provider_correlations"],
                "preservation": support["target_only_preservation"],
                "action": action is not None,
                "citations_present": action is not None and bool(action.citation_ids),
                "citations_known": action is not None
                and set(action.citation_ids) <= citations,
                "combinations_known": action is not None
                and set(action.combination_ids) <= combinations,
                "eligible": None if action is None else bool(
                    D._candidate_is_eligible(packet, proposal, candidate, action)),
            })
        proposals.append({
            "destination": destination,
            "outcome": proposal["outcome"],
            "reasons": reasons,
            "current": proposal["current"],
            "sufficiency": proposal["decision_summary"]["evidence_sufficiency"],
            "preservation": proposal["decision_summary"]["target_only_preservation"],
            "report_only": destination in report_only,
            "consensus_title": destination == "title" and len(reasons) == 1
            and reasons[0] in consensus,
            "tidy_title": destination == "title" and len(reasons) == 1
            and reasons[0] in tidy,
            "evidence_led": led is not None and reasons == [led],
            "reason": decided.get(proposal["proposal_id"]),
            "candidates": rows,
        })
    cases[case_id] = proposals
sys.stdout.write(json.dumps({"engine": engine, "cases": cases, "failed": failed}))
"""


def _optional_str(value: object) -> str | None:
    return None if value is None else ledger._exact(str, value)


def _candidate(data: Mapping[str, object]) -> Candidate:
    eligible = data["eligible"]
    return Candidate(
        candidate_id=ledger._exact(str, data["candidate_id"]),
        value=data["value"],
        scope=ledger._exact(str, data["scope"]),
        date_event=_optional_str(data["date_event"]),
        date_precision=_optional_str(data["date_precision"]),
        assessment=ledger._exact(str, data["assessment"]),
        evidence_references=tuple(
            ledger._exact(str, item)
            for item in cast("list[object]", data["evidence_references"])
        ),
        evidence_scope=ledger._exact(str, data["evidence_scope"]),
        binding=ledger._exact(str, data["binding"]),
        recording=ledger._exact(bool, data["recording"]),
        narrator_agreement=ledger._exact(bool, data["narrator_agreement"]),
        independent_title=ledger._exact(bool, data["independent_title"]),
        has_publication=ledger._exact(bool, data["has_publication"]),
        publication_event=_optional_str(data["publication_event"]),
        publication_consistency=_optional_str(data["publication_consistency"]),
        local=ledger._exact(int, data["local"]),
        correlations=ledger._exact(int, data["correlations"]),
        preservation=ledger._exact(int, data["preservation"]),
        action=ledger._exact(bool, data["action"]),
        citations_present=ledger._exact(bool, data["citations_present"]),
        citations_known=ledger._exact(bool, data["citations_known"]),
        combinations_known=ledger._exact(bool, data["combinations_known"]),
        eligible=None if eligible is None else ledger._exact(bool, eligible),
    )


def _proposal(data: Mapping[str, object]) -> Proposal:
    return Proposal(
        destination=ledger._exact(str, data["destination"]),
        outcome=ledger._exact(str, data["outcome"]),
        reasons=tuple(
            ledger._exact(str, item) for item in cast("list[object]", data["reasons"])
        ),
        current=data["current"],
        sufficiency=ledger._exact(str, data["sufficiency"]),
        preservation=ledger._exact(int, data["preservation"]),
        report_only=ledger._exact(bool, data["report_only"]),
        consensus_title=ledger._exact(bool, data["consensus_title"]),
        tidy_title=ledger._exact(bool, data["tidy_title"]),
        evidence_led=ledger._exact(bool, data["evidence_led"]),
        reason=_optional_str(data["reason"]),
        candidates=tuple(
            _candidate(cast("dict[str, object]", item))
            for item in cast("list[object]", data["candidates"])
        ),
    )


def parse_probe(text: str) -> A6Probe:
    """Validate the child's document; any surprise is a fixed refusal."""
    try:
        loaded = cast("dict[str, object]", json.loads(text))
        engine = cast("dict[str, object]", loaded["engine"])
        version = ledger._exact(str, engine["version"])
        scalar = frozenset(
            ledger._exact(str, item) for item in cast("list[object]", engine["scalar"])
        )
        cases = {
            ledger._exact(str, case_id): tuple(
                _proposal(cast("dict[str, object]", item))
                for item in cast("list[object]", proposals)
            )
            for case_id, proposals in cast(
                "dict[object, object]", loaded["cases"]
            ).items()
        }
        probe = A6Probe(
            version,
            scalar,
            ledger._exact(bool, engine["rich_split"]),
            cases,
            ledger._exact(int, loaded["failed"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ledger.Refusal("decision replay failed") from None
    if not _ENGINE.match(version) or not scalar <= ledger._ALL:
        raise ledger.Refusal("decision replay returned an unknown vocabulary")
    return probe


def child_a6(source_root: Path, database: Path) -> A6Probe:
    """Replay every current case's decision under its source in a child."""
    if not (source_root / "src" / "mechanical_librarian").is_dir():
        raise ledger.Refusal("source root is not an exported source tree")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _A6_CHILD,
                str(source_root),
                str(database),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=7200,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ledger.Refusal("decision replay failed") from None
    if completed.returncode != 0:
        raise ledger.Refusal("decision replay failed")
    return parse_probe(completed.stdout)


# ---------------------------------------------------------------------------
# Mirrored checks.


def table_for(version: str) -> Table | None:
    """The clause table that mirrors a source engine, or None when unmirrored.

    v22 has its own table. v23 added the degraded-candidate refusal (#1283),
    and every version from v23 through the checkout's v27 shares that one
    _candidate_is_eligible.
    """
    matched = _ENGINE.match(version)
    if matched is None:
        return None
    number = int(matched.group(1))
    if number == 9:
        return "v9"
    if number == 22:
        return "v22"
    if 23 <= number <= 27:
        return "v23"
    return None


def language_conflict(proposal: Proposal) -> bool:
    return (
        proposal.destination == "language"
        and "field_policy_conflict" in proposal.reasons
    )


def failing_checks(
    proposal: Proposal, candidate: Candidate, table: Table, report_only: bool
) -> tuple[str, ...]:
    """Every named check of _candidate_is_eligible the candidate fails, in order.

    Eligibility is the conjunction of these checks, so a candidate is
    eligible exactly when the tuple is empty.
    """
    destination = proposal.destination
    failing: set[str] = set()
    publication = destination == _PUBLICATION
    if table == "v9":
        work = destination in _WORK
        expected_scope = "work" if work else "edition"
        expected_binding = "associated_work_only" if work else "established"
        field_support = False
        if (
            not report_only and proposal.preservation != 0
        ) or candidate.preservation != 0:
            failing.add("preservation")
        if not publication and candidate.local != 1:
            failing.add("support_local")
        if not report_only and candidate.correlations < 1:
            failing.add("support_correlation")
        expected_event = "manifestation_publication"
    else:
        work_publication = (
            publication and candidate.date_event == "work_first_publication"
        )
        work = destination in _WORK or work_publication
        expected_scope = "work" if work else "edition"
        expected_binding = "associated_work_only" if work else "established"
        agreement = destination == "narrators" and candidate.narrator_agreement
        field_support = candidate.recording or agreement or work_publication
        if agreement:
            expected_binding = "associated_work_only"
        if destination == "narrators" and not field_support:
            failing.add("narrator")
        if proposal.evidence_led and not candidate.independent_title:
            failing.add("evidence_led_title")
        election = proposal.consensus_title or proposal.tidy_title
        if (
            not report_only
            and destination != "authors"
            and not field_support
            and not candidate.independent_title
            and proposal.preservation != (1 if election else 0)
        ) or candidate.preservation != 0:
            failing.add("preservation")
        if (
            destination == "authors"
            and not candidate.recording
            and candidate.local == 0
            and candidate.correlations < 2
        ):
            failing.add("support_correlation")
        if (
            destination not in {"authors", _PUBLICATION}
            and not field_support
            and not proposal.tidy_title
            and not candidate.independent_title
            and candidate.local != (0 if proposal.consensus_title else 1)
        ):
            failing.add("support_local")
        minimum = 2 if proposal.consensus_title and not field_support else 1
        if (
            not report_only
            and destination != "authors"
            and candidate.correlations < minimum
        ):
            failing.add("support_correlation")
        expected_event = (
            "work_first_publication"
            if work_publication
            else "manifestation_publication"
        )
        if table == "v23" and degraded(proposal, candidate):
            failing.add("degraded")
    if candidate.scope != expected_scope or candidate.evidence_scope != expected_scope:
        failing.add("scope")
    if candidate.binding != expected_binding:
        failing.add("binding")
    if not (
        candidate.citations_present
        and candidate.citations_known
        and candidate.combinations_known
    ):
        failing.add("citations")
    if publication:
        if (
            not candidate.has_publication
            or candidate.date_event != expected_event
            or candidate.publication_event != candidate.date_event
            or candidate.publication_consistency != "consistent"
            or candidate.date_precision not in _PRECISIONS
        ):
            failing.add("publication")
        if not field_support and candidate.local != 1:
            failing.add("support_local")
        if candidate.correlations < (1 if field_support else 2):
            failing.add("support_correlation")
    return tuple(check for check in CHECKS if check in failing)


def evaluated(
    proposal: Proposal, candidate: Candidate, table: Table, report_only: bool
) -> bool:
    """Whether _eligible_actions runs the eligibility checks on this candidate."""
    if table in _V22_FAMILY and language_conflict(proposal):
        return False
    if proposal.sufficiency == "keep_current":
        return candidate.assessment == "preserves_target"
    return (
        proposal.sufficiency == "candidate"
        or report_only
        or (
            table in _V22_FAMILY and any(item.recording for item in proposal.candidates)
        )
    )


def _main_report_only(destination: str) -> bool:
    return destination in shadow_decision._REPORT_ONLY_SOURCE_DESTINATIONS


def degraded(proposal: Proposal, candidate: Candidate) -> bool:
    """The checkout's own degraded-candidate refusal (#1283)."""
    return (
        shadow_decision._degraded_candidate_reason(
            {"destination": proposal.destination, "current": proposal.current},
            {"value": candidate.value},
        )
        is not None
    )


def preserves_current(proposal: Proposal, candidate: Candidate) -> bool:
    return shadow_decision._packet_value(
        candidate.value
    ) == shadow_decision._current_value({"current": proposal.current})


def attribute(proposal: Proposal, table: Table | None) -> tuple[list[str], int, int]:
    """Each candidate's first failing check or state, plus mirror agreement.

    Returns the attributions, the candidates compared with the source's own
    verdict, and how many of those the mirror disagreed with.
    """
    if not proposal.candidates:
        return ["no_candidate"], 0, 0
    if table is None:
        return ["not_mirrored"] * len(proposal.candidates), 0, 0
    if table in _V22_FAMILY and language_conflict(proposal):
        return ["language_conflict"], 0, 0
    states: list[str] = []
    compared = disagreed = 0
    for candidate in proposal.candidates:
        failing = failing_checks(proposal, candidate, table, proposal.report_only)
        if candidate.eligible is not None:
            compared += 1
            disagreed += candidate.eligible != (not failing)
        if not candidate.action:
            states.append("no_action")
        elif not evaluated(proposal, candidate, table, proposal.report_only):
            states.append("not_evaluated")
        else:
            states.append(failing[0] if failing else "passes")
    return states, compared, disagreed


def relax(proposal: Proposal) -> tuple[str, Candidate | None]:
    """The relaxation outcome for one abstained field, under the checkout's checks.

    Evaluated only where the source's decision abstained with
    ``insufficient_support``; every other reason refuses before any check.
    """
    rule = RELAXATION_BY_DESTINATION.get(proposal.destination)
    if rule is None or proposal.reason != "insufficient_support":
        return "refused_reason", None
    if language_conflict(proposal):
        return "refused_conflict", None
    report_only = _main_report_only(proposal.destination)
    # Main's checks without the degraded refusal, which is applied below so
    # that a degraded candidate reads refused_degraded, not refused_none.
    candidates = [
        candidate
        for candidate in proposal.candidates
        if candidate.action and evaluated(proposal, candidate, "v22", report_only)
    ]
    failing = {
        candidate.candidate_id: failing_checks(proposal, candidate, "v22", report_only)
        for candidate in candidates
    }
    if any(
        not failing[candidate.candidate_id] and not degraded(proposal, candidate)
        for candidate in candidates
    ):
        return "main_passes", None
    admissible = [
        candidate
        for candidate in candidates
        if failing[candidate.candidate_id] == (rule.check,)
        and rule.relaxed(
            candidate.local, candidate.correlations, candidate.date_precision
        )
    ]
    clean = [candidate for candidate in admissible if not degraded(proposal, candidate)]
    if len(clean) == 1:
        return "admitted", clean[0]
    if len(clean) > 1:
        return "refused_ambiguous", None
    return ("refused_degraded" if admissible else "refused_none"), None


# ---------------------------------------------------------------------------
# Screens for an admitted candidate.


def _normal(value: object) -> object:
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    if isinstance(value, list):
        return frozenset(_normal(item) for item in cast("list[object]", value))
    return value


_CLAIM_KINDS: dict[str, frozenset[str]] = {
    "title": frozenset({"title"}),
    "authors": frozenset({"creator"}),
    "narrators": frozenset({"contributor"}),
    "publisher": frozenset({"publisher"}),
    "language": frozenset({"language"}),
    "publishedDate": frozenset({"publication_date", "date_event"}),
}


def _record_value(case: ledger.Case, record: str, destination: str) -> object:
    kinds = _CLAIM_KINDS.get(destination, frozenset())
    for provider in case.providers():
        for candidate in ledger._dicts(provider.get("candidates")):
            if candidate.get("identity") != record:
                continue
            values = [
                claim.get("value")
                for claim in ledger._dicts(candidate.get("claims"))
                if claim.get("kind") in kinds and isinstance(claim.get("value"), str)
            ]
            if not values:
                return None
            if destination in {"authors", "narrators"}:
                return _normal(values)
            return _normal(values[0])
    return None


def value_agreement(
    case: ledger.Case, destination: str, candidate: Candidate
) -> bool | None:
    """Whether an identifier-accepted record the candidate does not cite agrees.

    None when no such record states a value for the destination: the screen
    then has nothing to compare.
    """
    cited = case.resolve(candidate.evidence_references)
    accepted = {
        identity
        for evidence in case.identifier_evidence()
        if evidence.get("relation") == "accepted"
        and isinstance(identity := evidence.get("candidate_identity"), str)
        and identity in case.records
        and identity not in cited
    }
    admitted = _normal(shadow_decision._packet_value(candidate.value))
    values = [
        value
        for record in sorted(accepted)
        if (value := _record_value(case, record, destination)) is not None
    ]
    if not values:
        return None
    return any(value == admitted for value in values)


def field_class(
    case: ledger.Case, destination: str, candidate: Candidate
) -> tuple[str, bool]:
    """AO's class of the field and whether the admitted candidate is the right one."""
    alternatives = ledger._dicts(case.results.get(destination, {}).get("alternatives"))
    right = [item for item in alternatives if ledger._right_work(case, item)]
    if len(right) > 1:
        return "tied", False
    if len(right) == 1:
        return "discriminable", right[0].get("alternative_id") == candidate.candidate_id
    return "no_right_work", False


# ---------------------------------------------------------------------------
# Measurement.


@dataclass(slots=True)
class RelaxationTally:
    outcomes: Counter[str] = field(default_factory=Counter)
    true_positive: int = 0
    false_positive: int = 0
    unlabelled: int = 0
    unreached: int = 0
    classes: Counter[str] = field(default_factory=Counter)
    agrees: int = 0
    preserves_current: int = 0
    agreement: int = 0
    agreement_n: int = 0


@dataclass(slots=True)
class A6Tally:
    populations: Counter[tuple[str, str, str, str, str]] = field(
        default_factory=Counter
    )
    echo: Counter[tuple[str, str]] = field(default_factory=Counter)
    rich_residual: Counter[str] = field(default_factory=Counter)
    checks: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    relaxations: dict[tuple[str, str], RelaxationTally] = field(default_factory=dict)
    replay_disagreements: int = 0
    mirror_compared: int = 0
    mirror_disagreements: int = 0


def _alternatives(case: ledger.Case, destination: str) -> str:
    count = len(ledger._dicts(case.results.get(destination, {}).get("alternatives")))
    return ALTERNATIVE_CLASSES[min(count, 2)]


def _stored(shadow: Mapping[str, object], destination: str) -> list[dict[str, object]]:
    return [
        outcome
        for outcome in ledger._dicts(shadow.get("outcomes"))
        if outcome.get("destination") == destination
    ]


def _population(
    case: ledger.Case, destination: str, outcome: Mapping[str, object]
) -> str | None:
    if (
        outcome.get("disposition") != _ABSTENTION
        or outcome.get("reason_code") not in A6_REASONS
    ):
        return None
    if case.blocked(destination):
        return "blocked"
    stated = ledger._text(case.results.get(destination, {}).get("outcome"))
    if (
        stated in _PROPOSABLE_OUTCOMES
        and outcome.get("policy_destination") == _AUTOMATIC
    ):
        return "proposable"
    return None


def _screen(
    case: ledger.Case, proposal: Proposal, candidate: Candidate, tally: RelaxationTally
) -> None:
    records = case.resolve(candidate.evidence_references)
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
    kind, agrees = field_class(case, proposal.destination, candidate)
    tally.classes[kind] += 1
    tally.agrees += agrees
    if preserves_current(proposal, candidate):
        tally.preserves_current += 1
    agreement = value_agreement(case, proposal.destination, candidate)
    if agreement is not None:
        tally.agreement_n += 1
        tally.agreement += agreement


def measure_a6(
    connection: sqlite3.Connection, probe: A6Probe
) -> tuple[A6Tally, Counter[str]]:
    """Stream the current cases once and tally the A6 analysis."""
    tally = A6Tally()
    media: Counter[str] = Counter()
    table = table_for(probe.version)
    try:
        rows = connection.execute(ledger._CASES_SQL)
        for case_id, case_text, report_text, shadow_text, consideration_text in rows:
            shadow = ledger._document(shadow_text)
            case = ledger._build_case(
                ledger._document(case_text),
                ledger._document(report_text),
                shadow,
                ledger._document(consideration_text),
            )
            media[case.medium] += 1
            _measure_case(tally, case, shadow, probe, table, probe.cases.get(case_id))
    except sqlite3.Error:
        raise ledger.Refusal("snapshot schema unrecognised") from None
    return tally, media


def _measure_case(
    tally: A6Tally,
    case: ledger.Case,
    shadow: Mapping[str, object],
    probe: A6Probe,
    table: Table | None,
    replayed: tuple[Proposal, ...] | None,
) -> None:
    for destination in sorted(case.applicable(ledger._ALL - probe.scalar)):
        if any(
            outcome.get("disposition") == _ABSTENTION
            and outcome.get("reason_code") == RICH_RESIDUAL_REASON
            for outcome in _stored(shadow, destination)
        ):
            tally.rich_residual[destination] += 1
    for destination in sorted(case.applicable(probe.scalar)):
        for outcome in _stored(shadow, destination):
            if (
                outcome.get("disposition") == _ABSTENTION
                and outcome.get("reason_code") == ECHO_REASON
                and case.blocked(destination)
            ):
                tally.echo[(destination, case.medium)] += 1
            population = _population(case, destination, outcome)
            if population is None:
                continue
            reason = cast("str", outcome["reason_code"])
            tally.populations[
                (
                    population,
                    destination,
                    reason,
                    case.medium,
                    _alternatives(case, destination),
                )
            ] += 1
            matches = [
                item for item in replayed or () if item.destination == destination
            ]
            if len(matches) != 1:
                tally.checks[(population, reason, "not_replayed")] += 1
                if _relaxed(destination, reason):
                    _relaxation(tally, population, destination).outcomes[
                        "not_replayed"
                    ] += 1
                continue
            proposal = matches[0]
            tally.replay_disagreements += proposal.reason != reason
            states, compared, disagreed = attribute(proposal, table)
            tally.mirror_compared += compared
            tally.mirror_disagreements += disagreed
            for state in states:
                tally.checks[(population, reason, state)] += 1
            if _relaxed(destination, reason):
                result = _relaxation(tally, population, destination)
                verdict, admitted = relax(proposal)
                result.outcomes[verdict] += 1
                if admitted is not None:
                    _screen(case, proposal, admitted, result)


def _relaxed(destination: str, reason: str) -> bool:
    return destination in RELAXATION_BY_DESTINATION and reason == "insufficient_support"


def _relaxation(tally: A6Tally, population: str, destination: str) -> RelaxationTally:
    rule = RELAXATION_BY_DESTINATION.get(destination)
    key = (population, rule.rule if rule is not None else destination)
    return tally.relaxations.setdefault(key, RelaxationTally())


# ---------------------------------------------------------------------------
# Closed output.


def _relaxation_entry(tally: RelaxationTally) -> dict[str, object]:
    total = tally.true_positive + tally.false_positive
    lower = ledger.wilson_lower_bound(tally.true_positive, total)
    return {
        "outcomes": {name: tally.outcomes.get(name, 0) for name in OUTCOMES},
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
    }


def _merge(tallies: Iterable[RelaxationTally]) -> RelaxationTally:
    merged = RelaxationTally()
    for tally in tallies:
        merged.outcomes.update(tally.outcomes)
        merged.true_positive += tally.true_positive
        merged.false_positive += tally.false_positive
        merged.unlabelled += tally.unlabelled
        merged.unreached += tally.unreached
        merged.classes.update(tally.classes)
        merged.agrees += tally.agrees
        merged.preserves_current += tally.preserves_current
        merged.agreement += tally.agreement
        merged.agreement_n += tally.agreement_n
    return merged


def snapshot_entry(
    label: str,
    probe: A6Probe,
    tally: A6Tally,
    human_decisions: int,
) -> dict[str, object]:
    populations = [
        {
            "population": population,
            "destination": destination,
            "reason": reason,
            "medium": medium,
            "alternatives": alternatives,
            "fields": count,
        }
        for (population, destination, reason, medium, alternatives), count in sorted(
            tally.populations.items(),
            key=lambda item: (POPULATIONS.index(item[0][0]), item[0][1:]),
        )
    ]
    checks = [
        {
            "population": population,
            "reason": reason,
            "check": check,
            "candidates": count,
        }
        for (population, reason, check), count in sorted(
            tally.checks.items(),
            key=lambda item: (
                POPULATIONS.index(item[0][0]),
                item[0][1],
                (*CHECKS, *STATES).index(item[0][2]),
            ),
        )
    ]
    relaxations = [
        {"population": population, "rule": rule, **_relaxation_entry(result)}
        for (population, rule), result in sorted(
            tally.relaxations.items(),
            key=lambda item: (
                POPULATIONS.index(item[0][0]),
                [value.rule for value in RELAXATIONS].index(item[0][1]),
            ),
        )
    ]
    return {
        "label": label,
        "engine": probe.version,
        "scalar_destinations": sorted(probe.scalar),
        "rich_split": probe.rich_split,
        "human_decisions": human_decisions,
        "replay_failed_cases": probe.failed,
        "replay_disagreements": tally.replay_disagreements,
        "mirror_compared": tally.mirror_compared,
        "mirror_disagreements": tally.mirror_disagreements,
        "populations": populations,
        "echo": [
            {"destination": destination, "medium": medium, "fields": count}
            for (destination, medium), count in sorted(tally.echo.items())
        ],
        "rich_residual": dict(sorted(tally.rich_residual.items()))
        if probe.rich_split
        else None,
        "sub_checks": checks,
        "relaxations": relaxations,
    }


def pooled_relaxations(
    tallies: Sequence[A6Tally],
) -> list[dict[str, object]]:
    pooled: list[dict[str, object]] = []
    for rule in RELAXATIONS:
        merged = _merge(
            result
            for tally in tallies
            for (_population, name), result in tally.relaxations.items()
            if name == rule.rule
        )
        pooled.append(
            {
                "rule": rule.rule,
                "destination": rule.destination,
                **_relaxation_entry(merged),
            }
        )
    return pooled


def human_decisions(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("SELECT count(*) FROM human_decisions").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row is not None else 0


VOCABULARY = frozenset(
    {
        KEY,
        "engine",
        "scalar_destinations",
        "rich_split",
        "human_decisions",
        "replay_failed_cases",
        "replay_disagreements",
        "mirror_compared",
        "mirror_disagreements",
        "populations",
        "population",
        "destination",
        "reason",
        "alternatives",
        "echo",
        "rich_residual",
        "sub_checks",
        "check",
        "candidates",
        "relaxations",
        "rule",
        "outcomes",
        "field_classes",
        "preserves_current",
        "value_agreement",
        "value_agreement_n",
        "pooled_relaxations",
        *POPULATIONS,
        *ALTERNATIVE_CLASSES,
        *CHECKS,
        *STATES,
        *OUTCOMES,
        *FIELD_CLASSES,
        *(rule.rule for rule in RELAXATIONS),
    }
)


def vocabulary(section: Mapping[str, object]) -> frozenset[str]:
    """The analysis's closed strings, plus each validated engine version."""
    engines = {
        cast("str", entry["engine"])
        for entry in ledger._dicts(section.get("snapshots"))
        if _ENGINE.match(cast("str", entry["engine"]))
    }
    return VOCABULARY | engines


# ---------------------------------------------------------------------------
# Markdown.


def _n(value: object) -> str:
    return f"{cast('int', value):,}"


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


def _classes(entry: Mapping[str, object]) -> str:
    classes = cast("dict[str, int]", entry["field_classes"])
    return (
        f"{_n(classes['tied'])} / {_n(classes['discriminable'])} "
        f"({_n(entry['pick_agrees'])} right-work) / {_n(classes['no_right_work'])}"
    )


def _relaxation_cells(entry: Mapping[str, object]) -> list[str]:
    outcomes = cast("dict[str, int]", entry["outcomes"])
    # Fields whose case the source could not replay are counted in the engine
    # table, not here.
    evaluated_fields = sum(outcomes.values()) - outcomes["not_replayed"]
    return [
        _n(evaluated_fields),
        _n(outcomes["main_passes"]),
        f"{_n(outcomes['refused_none'])} / {_n(outcomes['refused_degraded'])} / "
        f"{_n(outcomes['refused_ambiguous'])} / {_n(outcomes['refused_conflict'])}",
        _n(outcomes["admitted"]),
        f"{entry['true_positive']}/{entry['false_positive']}/{entry['unlabelled']}",
        _ratio(entry),
        _classes(entry),
        _n(entry["preserves_current"]),
        _agreement(entry),
    ]


def render(section: Mapping[str, object]) -> list[str]:
    snapshots = ledger._dicts(section.get("snapshots"))
    lines = [
        "### Replay",
        "",
        "| Snapshot | Decision engine | Scalar fields | Rich-preview reasons | "
        "Owner decisions | Cases not replayed | Replayed reasons that differ | "
        "Mirror disagreements with the source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        lines.append(
            f"| {entry['label']} | {entry['engine']} | "
            f"{', '.join(cast('list[str]', entry['scalar_destinations']))} | "
            + ("separate reasons" if entry["rich_split"] else "collapsed")
            + f" | {_n(entry['human_decisions'])} | {_n(entry['replay_failed_cases'])} | "
            f"{_n(entry['replay_disagreements'])} | "
            f"{_n(entry['mirror_disagreements'])} of {_n(entry['mirror_compared'])} |"
        )
    lines += [
        "",
        "### Populations",
        "",
        "| Snapshot | Population | Destination | Reason | Medium | "
        "Fields (no / one / more alternatives) |",
        "|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        grouped: dict[tuple[str, str, str, str], Counter[str]] = {}
        for row in ledger._dicts(entry["populations"]):
            key = (
                cast("str", row["population"]),
                cast("str", row["destination"]),
                cast("str", row["reason"]),
                cast("str", row["medium"]),
            )
            grouped.setdefault(key, Counter())[cast("str", row["alternatives"])] += (
                cast("int", row["fields"])
            )
        for (population, destination, reason, medium), classes in grouped.items():
            lines.append(
                f"| {entry['label']} | {population} | {destination} | {reason} | {medium} | "
                f"{_n(sum(classes.values()))} ({_n(classes['none'])} / "
                f"{_n(classes['one'])} / {_n(classes['more'])}) |"
            )
    lines += [
        "",
        "### Upstream echo and rich-preview leftover",
        "",
        "| Snapshot | Upstream echo (insufficient evidence), blocked fields | "
        "Rich-preview no-eligible-action leftover, all fields |",
        "|---|---|---|",
    ]
    for entry in snapshots:
        echo = sum(cast("int", row["fields"]) for row in ledger._dicts(entry["echo"]))
        residual = entry["rich_residual"]
        lines.append(
            f"| {entry['label']} | {_n(echo)} | "
            + (
                "not separable: rich-preview refusals collapsed"
                if residual is None
                else _n(sum(cast("dict[str, int]", residual).values()))
            )
            + " |"
        )
    lines += [
        "",
        "### First failing check",
        "",
        "| Snapshot | Population | Reason | First failing check or state | Candidates |",
        "|---|---|---|---|---|",
    ]
    for entry in snapshots:
        for row in ledger._dicts(entry["sub_checks"]):
            lines.append(
                f"| {entry['label']} | {row['population']} | {row['reason']} | "
                f"{row['check']} | {_n(row['candidates'])} |"
            )
    header = (
        "| Relaxation | Snapshot | Population | Evaluated | Main already passes | "
        "Refused: none / degraded / ambiguous / conflict | Admitted | "
        "Cited records: right work / wrong work / unlabelled | "
        "Identity precision (Wilson 95% lower limit) | "
        "Fields: tied / decided (admitted is the right-work one) / no right-work | "
        "Repeats current value | Value agreement with an uncited record |"
    )
    lines += ["", "### Relaxations", "", header, "|" + "---|" * 12]
    for entry in snapshots:
        for row in ledger._dicts(entry["relaxations"]):
            lines.append(
                f"| {row['rule']} | {entry['label']} | {row['population']} | "
                + " | ".join(_relaxation_cells(row))
                + " |"
            )
    for row in ledger._dicts(section.get("pooled_relaxations")):
        lines.append(
            f"| {row['rule']} | pooled | both | "
            + " | ".join(_relaxation_cells(row))
            + " |"
        )
    return lines
