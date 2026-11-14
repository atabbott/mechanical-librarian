"""The Book matching level replay of the admission-rule ledger (#1355).

``--work-identity-levels`` replays every current case's report under THIS
checkout's code, never the snapshot's own source: the levels exist only here
(ADR-0096), so an own-source replay cannot evaluate them. Each report is read
from the snapshot as stored and parsed by the current report parser. A report
one schema behind (``metadata-report-v79``) is upgraded with the closed
defaults the current schema added: no retained refused WORK answers, none
truncated, and no refused identity added by any lane. A report on any other
schema is counted as not replayed, never guessed at.

For each replayed case the module checks identity first: the live proposal
set derived with no level equals the one derived at ``identifier``, byte for
byte, and ``identifier`` produces no level evidence. Then, for each wider
level, it counts:

- the provider records the level admits, live records and retained refused
  answers apart, and how many of them only the dotted-initials fold admits;
- the records it refuses, by refusal: ``refused`` (the predicate or a
  sibling veto), ``ambiguous`` (more than one work met the level in one set)
  and ``truncated`` (the observation's refused set lost records);
- per field the level reads, the fields whose alternatives change, the ones
  that gain a choice where live had none, the manual fields the level offer
  substitutes, and the would-admit notes.

The proposal sets are evaluated with the built-in policy, so a snapshot's own
policy choices are not replayed. Everything leaves this module as counts in
the ledger's closed vocabulary; values and identifiers stay in memory.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from mechanical_librarian.domain.level_proposal_set import (
    LEVEL_OFFER_DESTINATIONS,
    derive_level,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    REPORT_SCHEMA,
    MetadataReport,
    canonical_json,
    provider_supplies_field_evidence,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    evaluate_report,
    level_evidence,
    provider_candidate_level_admission,
    provider_candidate_represents_target_work,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

# Direct execution puts scripts/ on sys.path and the ledger aliases itself
# there before importing this module; the test import resolves the scripts
# namespace package.
try:
    from scripts import admission_rule_ledger as ledger
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import admission_rule_ledger as ledger  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

KEY = "work_identity_levels"
IDENTIFIER = WorkIdentityLevel.IDENTIFIER.value
LEVELS = tuple(
    value.value
    for value in WorkIdentityLevel
    if value is not WorkIdentityLevel.IDENTIFIER
)
REFUSALS = ("refused", "ambiguous", "truncated")
FIELD_COUNTS = ("changed", "gained", "substituted", "would_admit")
FIELDS = tuple(LEVEL_OFFER_DESTINATIONS)
NOT_REPLAYED = ("schema_unsupported", "replay_failed")
# The one predecessor schema whose upgrade is a closed set of defaults.
PREDECESSOR_SCHEMA = "metadata-report-v79"
NOT_ESTIMABLE = "not estimable"

_CURRENT_REPORTS_SQL = (
    "SELECT mr.document FROM review_cases rc "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
    f"WHERE {ledger._CURRENT_SQL} ORDER BY rc.review_case_id"
)


@dataclass(slots=True)
class LevelTally:
    """One wider level's counts over one snapshot's replayed cases."""

    cases_with_admission: int = 0
    admitted_live: int = 0
    admitted_retained: int = 0
    initials_fold: int = 0
    refusals: Counter[str] = field(default_factory=Counter)
    fields: dict[str, Counter[str]] = field(
        default_factory=lambda: {destination: Counter() for destination in FIELDS}
    )


@dataclass(slots=True)
class LevelsProbe:
    """One snapshot's replay: counts only."""

    cases: int = 0
    upgraded: int = 0
    not_replayed: Counter[str] = field(default_factory=Counter)
    identity_differences: int = 0
    retained_refused: int = 0
    retained_truncated: int = 0
    levels: dict[str, LevelTally] = field(
        default_factory=lambda: {level: LevelTally() for level in LEVELS}
    )

    @property
    def replayed(self) -> int:
        return self.cases - sum(self.not_replayed.values())


LevelsReader = Callable[[Path], LevelsProbe]


def upgrade_predecessor(document: Mapping[str, object]) -> str:
    """A v79 report as v80 with the closed defaults v80 added (#1355).

    v80 added, per provider observation, ``identity_refused_candidates`` and
    ``identity_refused_truncated``, and per lane ``identity_refused_new``. A
    v79 report retained no refused answer, so each is empty. The report's
    content address is recomputed over the upgraded document.
    """
    data = cast("dict[str, object]", json.loads(json.dumps(document)))
    for provider in ledger._dicts(data["providers"]):
        if "identity_refused_candidates" in provider:
            raise ValueError("predecessor report already carries a v80 field")
        provider["identity_refused_candidates"] = []
        provider["identity_refused_truncated"] = 0
        for lane in ledger._dicts(provider["lanes"]):
            lane["identity_refused_new"] = []
    data["schema_version"] = REPORT_SCHEMA
    data.pop("report_id")
    data["report_id"] = report_content_address(data)
    return canonical_json(data)


def parse_report(text: str) -> tuple[MetadataReport | None, bool]:
    """The report under the current parser, and whether it was upgraded.

    ``None`` is a schema this module does not upgrade.
    """
    schema = ledger._dict(json.loads(text)).get("schema_version")
    if schema == REPORT_SCHEMA:
        return MetadataReport.from_json(text), False
    if schema == PREDECESSOR_SCHEMA:
        document = ledger._dict(json.loads(text))
        return MetadataReport.from_json(upgrade_predecessor(document)), True
    return None, False


def _identical(report: MetadataReport, policy_id: str) -> tuple[ProposalSet, bool]:
    live = evaluate_report(report, policy_id)
    at_identifier = evaluate_report(
        report, policy_id, work_identity_level=WorkIdentityLevel.IDENTIFIER
    )
    return live, (
        live.to_json() == at_identifier.to_json()
        and level_evidence(report, WorkIdentityLevel.IDENTIFIER) is None
    )


def _count_records(
    report: MetadataReport, level: WorkIdentityLevel, tally: LevelTally
) -> bool:
    """Tally every record's level verdict; whether any record was admitted."""
    admitted = False
    for provider in report.providers:
        if not provider_supplies_field_evidence(report, provider):
            continue
        records = [
            (candidate, "live")
            for candidate in provider.candidates
            if not provider_candidate_represents_target_work(
                report, provider, candidate
            )
        ]
        records += [
            (candidate, "retained")
            for candidate in provider.identity_refused_candidates
        ]
        for candidate, origin in records:
            record = provider_candidate_level_admission(
                report, provider, candidate, level
            )
            if record is None:
                continue
            if record.admission is None:
                tally.refusals[cast("str", record.refusal)] += 1
                continue
            admitted = True
            if origin == "live":
                tally.admitted_live += 1
            else:
                tally.admitted_retained += 1
            if record.creator_comparison == "initials_fold":
                tally.initials_fold += 1
    return admitted


def _count_fields(
    report: MetadataReport,
    live: ProposalSet,
    policy: ResolvedMetadataPolicy,
    level: WorkIdentityLevel,
    tally: LevelTally,
) -> None:
    level_set = evaluate_report(
        report, live.resolved_policy_id, work_identity_level=level
    )
    live_results = {value.destination.value: value for value in live.results}
    level_results = {value.destination.value: value for value in level_set.results}
    for destination in FIELDS:
        before = tuple(
            value.to_data() for value in live_results[destination].alternatives
        )
        after = tuple(
            value.to_data() for value in level_results[destination].alternatives
        )
        if before != after:
            tally.fields[destination]["changed"] += 1
            if not before:
                tally.fields[destination]["gained"] += 1
    case = ReviewCase.create(live, report)
    derivation = derive_level(report, live, case, policy, 1, level)
    if derivation.offer is not None:
        for entry in ledger._dicts(
            ledger._dict(json.loads(derivation.offer))["fields"]
        ):
            tally.fields[cast("str", entry["destination"])]["substituted"] += 1
    for note in derivation.notes:
        tally.fields[note.field]["would_admit"] += 1


def replay_report(text: str, probe: LevelsProbe) -> None:
    """Replay one current case's report into the probe's counts."""
    probe.cases += 1
    try:
        report, upgraded = parse_report(text)
    except Exception:
        probe.not_replayed["replay_failed"] += 1
        return
    if report is None:
        probe.not_replayed["schema_unsupported"] += 1
        return
    try:
        policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
        live, identical = _identical(report, policy.resolved_id)
        tallies: dict[str, LevelTally] = {level: LevelTally() for level in LEVELS}
        admissions: dict[str, bool] = {}
        for value in LEVELS:
            level = WorkIdentityLevel(value)
            admissions[value] = _count_records(report, level, tallies[value])
            _count_fields(report, live, policy, level, tallies[value])
    except Exception:
        probe.not_replayed["replay_failed"] += 1
        return
    probe.upgraded += upgraded
    probe.identity_differences += not identical
    for provider in report.providers:
        probe.retained_refused += len(provider.identity_refused_candidates)
        probe.retained_truncated += provider.identity_refused_truncated
    for value in LEVELS:
        _merge(probe.levels[value], tallies[value], admissions[value])


def _merge(total: LevelTally, case: LevelTally, admitted: bool) -> None:
    total.cases_with_admission += admitted
    total.admitted_live += case.admitted_live
    total.admitted_retained += case.admitted_retained
    total.initials_fold += case.initials_fold
    total.refusals.update(case.refusals)
    for destination in FIELDS:
        total.fields[destination].update(case.fields[destination])


def replay_levels(database: Path) -> LevelsProbe:
    """Replay every current case's report in the snapshot under this checkout."""
    connection = ledger._open_snapshot(database)
    probe = LevelsProbe()
    try:
        rows = connection.execute(_CURRENT_REPORTS_SQL).fetchall()
    except sqlite3.Error:
        raise ledger.Refusal("level replay failed") from None
    finally:
        connection.close()
    for (text,) in rows:
        replay_report(cast("str", text), probe)
    return probe


def _share(count: int, total: int) -> float | None:
    """A proportion of replayed cases, or ``None`` when none was replayed."""
    return None if total == 0 else round(count / total, 4)


def snapshot_entry(label: str, probe: LevelsProbe) -> dict[str, object]:
    replayed = probe.replayed
    return {
        "label": label,
        "cases": probe.cases,
        "replayed": replayed,
        "upgraded": probe.upgraded,
        "not_replayed": {reason: probe.not_replayed[reason] for reason in NOT_REPLAYED},
        "identity": {
            "level": IDENTIFIER,
            "compared": replayed,
            "differences": probe.identity_differences,
        },
        "retained_refused": probe.retained_refused,
        "retained_truncated": probe.retained_truncated,
        "levels": [
            {
                "level": value,
                "cases_with_admission": tally.cases_with_admission,
                "share_with_admission": _share(tally.cases_with_admission, replayed),
                "admitted_live": tally.admitted_live,
                "admitted_retained": tally.admitted_retained,
                "initials_fold": tally.initials_fold,
                "refusals": {reason: tally.refusals[reason] for reason in REFUSALS},
                "fields": [
                    {
                        "destination": destination,
                        **{
                            count: tally.fields[destination][count]
                            for count in FIELD_COUNTS
                        },
                    }
                    for destination in FIELDS
                ],
            }
            for value, tally in probe.levels.items()
        ],
    }


VOCABULARY = frozenset(
    {
        KEY,
        "cases",
        "replayed",
        "upgraded",
        "not_replayed",
        "identity",
        "compared",
        "differences",
        "retained_refused",
        "retained_truncated",
        "levels",
        "level",
        "cases_with_admission",
        "share_with_admission",
        "admitted_live",
        "admitted_retained",
        "initials_fold",
        "refusals",
        "fields",
        "destination",
        IDENTIFIER,
        *LEVELS,
        *REFUSALS,
        *FIELD_COUNTS,
        *FIELDS,
        *NOT_REPLAYED,
    }
)


def vocabulary(section: Mapping[str, object]) -> frozenset[str]:
    del section
    return VOCABULARY


# ---------------------------------------------------------------------------
# Markdown.


def _n(value: object) -> str:
    return f"{cast('int', value):,}"


def _share_cell(entry: Mapping[str, object]) -> str:
    share = entry["share_with_admission"]
    if share is None:
        return NOT_ESTIMABLE
    return f"{_n(entry['cases_with_admission'])} ({cast('float', share):.1%})"


def render(section: Mapping[str, object]) -> list[str]:
    snapshots = ledger._dicts(section.get("snapshots"))
    lines = [
        "### Replay at identifier",
        "",
        "| Snapshot | Current cases | Replayed | Upgraded from the previous report "
        "schema | Not replayed: schema / failed | Differences at identifier | "
        "Retained refused answers | Truncated |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        missing = cast("dict[str, int]", entry["not_replayed"])
        identity = ledger._dict(entry["identity"])
        differences = (
            NOT_ESTIMABLE
            if not identity["compared"]
            else f"{_n(identity['differences'])} of {_n(identity['compared'])}"
        )
        lines.append(
            f"| {entry['label']} | {_n(entry['cases'])} | {_n(entry['replayed'])} | "
            f"{_n(entry['upgraded'])} | {_n(missing['schema_unsupported'])} / "
            f"{_n(missing['replay_failed'])} | {differences} | "
            f"{_n(entry['retained_refused'])} | {_n(entry['retained_truncated'])} |"
        )
    lines += [
        "",
        "### Records per level",
        "",
        "| Snapshot | Level | Cases with an admission | Admitted: live / retained | "
        "Initials fold only | Refused: predicate or veto / ambiguous / truncated |",
        "|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        for row in ledger._dicts(entry["levels"]):
            refusals = cast("dict[str, int]", row["refusals"])
            lines.append(
                f"| {entry['label']} | {row['level']} | {_share_cell(row)} | "
                f"{_n(row['admitted_live'])} / {_n(row['admitted_retained'])} | "
                f"{_n(row['initials_fold'])} | {_n(refusals['refused'])} / "
                f"{_n(refusals['ambiguous'])} / {_n(refusals['truncated'])} |"
            )
    lines += [
        "",
        "### Fields per level",
        "",
        "| Snapshot | Level | Field | Alternatives change | Gains a choice | "
        "Substituted for the owner | Would-admit notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        replayed = cast("int", entry["replayed"])
        for row in ledger._dicts(entry["levels"]):
            for counts in ledger._dicts(row["fields"]):
                cells = (
                    [NOT_ESTIMABLE] * len(FIELD_COUNTS)
                    if not replayed
                    else [_n(counts[count]) for count in FIELD_COUNTS]
                )
                lines.append(
                    f"| {entry['label']} | {row['level']} | {counts['destination']} | "
                    + " | ".join(cells)
                    + " |"
                )
    return lines
