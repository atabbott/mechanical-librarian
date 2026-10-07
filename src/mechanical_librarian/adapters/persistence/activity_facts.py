"""Runtime-owned serialized Activity facts, admitted by exact SQLite witnesses."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, cast

from mechanical_librarian.domain.activity_records import ReadableActivityEvent
from mechanical_librarian.domain.readable_activity import ISBN_REFUSAL_REASONS

_TABLES = (
    "metadata_reports",
    "review_cases",
    "proposal_sets",
    "shadow_decision_outcomes",
    "automatic_decisions",
    "human_decisions",
    "human_decision_events",
    "cover_considerations",
    "cover_consideration_events",
    "cover_selection_authorities",
    "field_effect_events",
    "field_effect_no_changes",
    "metadata_policy_drafts",
    "description_advice_candidates",
    "description_decisions",
    "field_effects",
    "description_advice_attempts",
    "metadata_policy_transitions",
    "metadata_policy_resolutions",
    "current_cover_considerations",
)
Key = tuple[str, bytes]
Candidate = tuple[str, str]


def _encoded(values: object) -> bytes:
    def encode(value: object) -> object:
        if type(value) is bytes:
            return {"b": base64.b64encode(value).decode("ascii")}
        if type(value) is float:
            return {"f": value.hex()}
        if type(value) in (tuple, list):
            return [
                encode(item) for item in cast(tuple[object, ...] | list[object], value)
            ]
        return value

    return json.dumps(encode(values), ensure_ascii=True, separators=(",", ":")).encode()


def _key(table: str, *values: object) -> Key:
    return table, _encoded(values)


@dataclass(frozen=True, slots=True)
class _Witness:
    value: bytes
    dependencies: frozenset[Key]
    aliases: frozenset[Key]
    candidates: frozenset[Candidate]


@dataclass(frozen=True, slots=True)
class ActivityFact:
    event: ReadableActivityEvent
    search: str
    occurred_at: int
    event_type: str
    event_id: str


class ActivityFactCapacity(Exception):
    """The next serialized fact would exceed the process-owned content bound."""


def activity_fact_bytes(key: Candidate, fact: ActivityFact) -> int:
    return (
        len(key[0])
        + len(key[1])
        + len(fact.event.document)
        + len(fact.search.encode())
        + len(fact.event_type)
        + len(fact.event_id)
        + 16  # Timestamp and sorted-reference slot.
    )


@dataclass(frozen=True, slots=True, eq=False)
class _Snapshot:
    witnesses: dict[Key, _Witness]
    facts: dict[Candidate, ActivityFact]
    ordered: tuple[ActivityFact, ...]
    accounted_bytes: int
    version: object = field(default_factory=object)


def _bindings(
    table: str, row: dict[str, Any], connection: sqlite3.Connection
) -> tuple[frozenset[Key], frozenset[Key], frozenset[Candidate]]:
    dependencies: set[Key] = set()
    aliases: set[Key] = set()
    candidates: set[Candidate] = set()

    def dependency(name: str, *values: object) -> None:
        if all(value is not None for value in values):
            dependencies.add(_key(name, *values))

    def candidate(kind: str, identity: str) -> None:
        candidates.add((kind, identity))

    if table in {
        "review_cases",
        "proposal_sets",
        "shadow_decision_outcomes",
        "cover_considerations",
        "field_effect_no_changes",
    }:
        dependency("metadata_reports", row.get("report_id"))
    if table in {"review_cases", "shadow_decision_outcomes"}:
        dependency("proposal_sets", row.get("proposal_set_id"))
    if table in {
        "shadow_decision_outcomes",
        "automatic_decisions",
        "human_decisions",
        "human_decision_events",
        "cover_considerations",
        "cover_consideration_events",
        "field_effect_no_changes",
        "description_decisions",
    }:
        dependency("review_cases", row.get("review_case_id"))
    if table == "shadow_decision_outcomes":
        document = json.loads(row["document"])
        # Only ISBN-refusal membership reads policy authority. Keep every
        # outcome candidate below so a source change can enter that membership.
        if any(
            type(member) is dict
            and member.get("destination") == "isbn"
            and member.get("reason_code") in ISBN_REFUSAL_REASONS
            for member in document["outcomes"]
        ):
            case = connection.execute(
                "SELECT CAST(document AS BLOB) FROM review_cases WHERE review_case_id=?",
                (row["review_case_id"],),
            ).fetchone()
            if case is not None:
                dependency(
                    "isbn_resolution",
                    document["policy_generation"],
                    json.loads(case[0])["target"]["library"],
                )
            if document["policy_generation"] == 1:
                dependency("isbn_fallback", document["policy_profile_id"])
        aliases.add(
            _key(
                "outcome_identity",
                *(
                    row[name]
                    for name in (
                        "review_case_id",
                        "report_id",
                        "proposal_set_id",
                        "resolved_policy_id",
                    )
                ),
            )
        )
        candidate("isbn_refusal", row["outcome_set_id"] + ":isbn")
    elif table == "human_decisions":
        aliases.add(_key("human_identity", row["decision_id"]))
    elif table == "human_decision_events":
        dependency("human_identity", row["decision_id"])
        candidate("human_decision", row["event_id"])
    elif table == "automatic_decisions":
        document = json.loads(row["document"])
        dependency("metadata_reports", document["report_id"])
        dependency("proposal_sets", document["proposal_set_id"])
        dependency(
            "outcome_identity",
            *(
                document[name]
                for name in (
                    "review_case_id",
                    "report_id",
                    "proposal_set_id",
                    "resolved_policy_id",
                )
            ),
        )
        candidate("automatic_decision", row["decision_id"])
    elif table == "cover_consideration_events":
        dependency("cover_considerations", row["consideration_id"])
        dependency("cover_identity", row["decision_id"])
        candidate("cover_safeguard", row["event_id"])
    elif table == "cover_selection_authorities":
        dependency("cover_considerations", row["consideration_id"])
        aliases.add(_key("cover_identity", row["decision_id"]))
        if row["kind"] == "automatic":
            document = json.loads(json.loads(row["document"])["decision_document"])
            for name, field in (
                ("review_cases", "review_case_id"),
                ("metadata_reports", "report_id"),
                ("proposal_sets", "proposal_set_id"),
                ("current_cover_considerations", "review_case_id"),
            ):
                dependency(name, document[field])
            dependency(
                "resolution_identity",
                document["resolved_policy_id"],
                document["policy_generation"],
                row["library_name"],
            )
            candidate("automatic_decision", row["decision_id"])
    elif table == "field_effect_events":
        dependency("field_effects", row["plan_id"])
        candidate("field_effect", row["plan_id"])
    elif table == "field_effects":
        for name in (
            "human_identity",
            "automatic_decisions",
            "cover_identity",
            "description_decisions",
        ):
            dependency(name, row["decision_id"])
        candidate("field_effect", row["plan_id"])
    elif table == "field_effect_no_changes":
        candidate("field_effect_no_change", row["event_id"])
    elif table == "description_advice_attempts":
        document = json.loads(row["binding_document"])
        dependency("review_cases", document["review_case_id"])
        dependency("candidate_attempt", row["attempt_id"])
        candidate("description_preparation", row["attempt_id"])
    elif table == "description_advice_candidates":
        aliases.add(_key("candidate_attempt", row["attempt_id"]))
    elif table == "description_decisions":
        dependency("description_advice_candidates", row["candidate_id"])
        candidate("description_decision", row["decision_id"])
    elif table == "metadata_policy_transitions":
        dependency("metadata_policy_drafts", row["from_policy_id"])
        dependency("metadata_policy_drafts", row["to_policy_id"])
        candidate("policy_transition", row["transition_id"])
    elif table == "metadata_policy_drafts":
        aliases.add(_key("isbn_fallback", row["policy_id"]))
    elif table == "metadata_policy_resolutions":
        aliases.add(_key("isbn_resolution", row["generation"], row["library_name"]))
        aliases.add(
            _key(
                "resolution_identity",
                row["resolved_policy_id"],
                row["generation"],
                row["library_name"],
            )
        )
    return frozenset(dependencies), frozenset(aliases), frozenset(candidates)


class ActivityFactStore:
    """One root-lifetime store; readers borrow immutable published snapshots."""

    def __init__(self, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._lock = Lock()
        self._snapshot: _Snapshot | None = None
        self._disabled = False

    def snapshot(self) -> _Snapshot | None:
        with self._lock:
            return self._snapshot

    def refresh(
        self,
        connection: sqlite3.Connection,
        project: Callable[[set[Candidate] | None, int], dict[Candidate, ActivityFact]],
    ) -> _Snapshot | None:
        with self._lock:
            if self._disabled:
                return None
            previous = self._snapshot
        witnesses: dict[Key, _Witness] = {}
        accounted = 0
        # Stream the raw bytes from this borrower's SQLite snapshot. No object
        # parser or shared connection revision can stand in for this comparison.
        for table in _TABLES:
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            names = [column[1] for column in columns]
            primary = [
                i
                for _, i in sorted(
                    (column[5], i) for i, column in enumerate(columns) if column[5]
                )
            ]
            documents = [i for i, name in enumerate(names) if name.endswith("document")]
            selections = [
                f"CAST({name} AS BLOB)" if i in documents else name
                for i, name in enumerate(names)
            ]
            selections += [f"typeof({names[i]})" for i in documents]
            for row in connection.execute(
                f"SELECT {','.join(selections)} FROM {table}"
            ):
                key = _key(table, *(row[i] for i in primary))
                values = list(row)
                for i in documents:
                    if values[i] is not None:
                        values[i] = hashlib.sha256(values[i]).digest()
                value = _encoded(values)
                old = None if previous is None else previous.witnesses.get(key)
                # The ISBN policy lookup uses the case target's library. A
                # replaced/missing case must rebuild that binding even when
                # the outcome document itself is unchanged.
                case_changed = False
                if table == "shadow_decision_outcomes" and previous is not None:
                    case_key = _key("review_cases", row[names.index("review_case_id")])
                    case_changed = witnesses.get(case_key) != previous.witnesses.get(
                        case_key
                    )
                if old is not None and old.value == value and not case_changed:
                    witnesses[key] = old
                else:
                    # Only changed documents needed for a dependency binding
                    # are decoded, and never retained after this row.
                    decoded = dict(zip(names, row, strict=False))
                    dependencies, aliases, candidates = _bindings(
                        table, decoded, connection
                    )
                    witnesses[key] = _Witness(value, dependencies, aliases, candidates)
                admitted = witnesses[key]
                accounted += (
                    len(key[0])
                    + len(key[1])
                    + len(admitted.value)
                    + sum(
                        len(k[0]) + len(k[1])
                        for k in admitted.dependencies | admitted.aliases
                    )
                    + sum(len(k[0]) + len(k[1]) for k in admitted.candidates)
                )
                if accounted > self.max_bytes:
                    self._disable()
                    return None
        changed = (
            set(witnesses)
            if previous is None
            else {
                key
                for key in witnesses.keys() | previous.witnesses.keys()
                if witnesses.get(key) != previous.witnesses.get(key)
            }
        )
        if previous is not None and not changed:
            return previous
        affected: set[Candidate] | None
        if previous is None:
            affected = None
            facts: dict[Candidate, ActivityFact] = {}
        else:
            # Reverse edges are needed only while computing a changed revision.
            # Retain each forward binding once; discard this temporary traversal
            # index before parsing any affected canonical candidates.
            reverse: dict[Key, set[Key]] = defaultdict(set)
            currentness: dict[Key, set[Candidate]] = defaultdict(set)
            for nodes in (witnesses, previous.witnesses):
                for key, witness in nodes.items():
                    for dependency in witness.dependencies:
                        if dependency[0] in {
                            "current_cover_considerations",
                            "resolution_identity",
                            "isbn_resolution",
                            "isbn_fallback",
                        }:
                            # Currentness and ISBN policy lookups affect these
                            # candidates, not ordinary retained decision lineage.
                            currentness[dependency].update(witness.candidates)
                        else:
                            reverse[dependency].add(key)
                    reverse[key].update(witness.aliases)
            affected = set()
            pending = deque(changed)
            reached = set(changed)
            while pending:
                key = pending.popleft()
                affected.update(currentness.get(key, ()))
                for nodes in (witnesses, previous.witnesses):
                    reached_witness = nodes.get(key)
                    if reached_witness is not None:
                        affected.update(reached_witness.candidates)
                for dependent in reverse.get(key, ()):
                    if dependent not in reached:
                        pending.append(dependent)
                        reached.add(dependent)
            del reverse, currentness
            facts = {
                key: value
                for key, value in previous.facts.items()
                if key not in affected
            }
        accounted += sum(activity_fact_bytes(key, fact) for key, fact in facts.items())
        if accounted > self.max_bytes:
            self._disable()
            return None
        if affected is None or affected:
            try:
                refreshed = project(affected, self.max_bytes - accounted)
            except ActivityFactCapacity:
                self._disable()
                return None
            accounted += sum(
                activity_fact_bytes(key, fact) for key, fact in refreshed.items()
            )
            facts.update(refreshed)
        ordered = tuple(
            sorted(
                facts.values(),
                key=lambda fact: (fact.occurred_at, fact.event_id),
                reverse=True,
            )
        )
        result = _Snapshot(witnesses, facts, ordered, accounted)
        with self._lock:
            if self._snapshot is previous:
                self._snapshot = result
        return result

    def _disable(self) -> None:
        with self._lock:
            self._snapshot = None
            self._disabled = True
