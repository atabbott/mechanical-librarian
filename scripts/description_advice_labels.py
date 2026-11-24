"""Library links to the descriptions Automatic could write, and their score.

A workbench instrument, deliberately not part of the installed product: run it
from the checkout an install was built from, against a COPY of that install's
application database. It opens the copy read-only through the product's own
immutable, query-only reader, requests nothing from any model and writes
nothing (ADR-0099).

``sample`` prints one Library link per current description whose advice the
owner can choose and that Automatic would write without asking: ready, above
the description floor and not the same as the current description. Each of the
owner's decisions on those fields is one label. ``score`` reads every label the
copy holds, by the rule ADR-0099 states, and prints per adviser identity the
right and wrong labels, the Wilson 95% lower limit, whether it meets the
owner's bar of 0.90, and the labels the bar still needs.

The output holds links, counts, digests, engine and model only: never a title,
description, item identifier, library name or path. A review case identifier
is an opaque content hash.

An install whose settings come from ``/data/config.json`` saves none in the
database; pass a copy of that file with ``--config`` so the copy is read with
the description choices the app binds from it at start.

Usage:
    uv run python scripts/description_advice_labels.py sample \\
        --database /copy/application.sqlite3 [--config /copy/config.json] \\
        [--origin http://localhost:8080] [--limit 35]
    uv run python scripts/description_advice_labels.py score \\
        --database /copy/application.sqlite3 [--config /copy/config.json] \\
        [--format text|json]
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    SQLiteShadowPreviewRepository,
    open_shadow_preview_repository,
)
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    canonical_json,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.application.reviewer_contracts import openrouter_profile
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    AuthoredDescriptionDecision,
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
    DescriptionAdviceInput,
    DescriptionAdviceSettings,
    DescriptionPreferences,
    automatic_description_floor_met,
    description_digest,
    description_document,
    description_qualification_manifest,
)
from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.reviewer_access import (
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
)
from mechanical_librarian.infrastructure.configuration import load_configuration_file

# Direct execution puts scripts/ on sys.path; the test import resolves the
# scripts namespace package.
try:
    from scripts import admission_rule_ledger as ledger
    from scripts import database_copy
    from scripts import hardcover_series_sample as series_sample
    from scripts import relaxation_label_sample as relaxation_sample
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import admission_rule_ledger as ledger  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import database_copy  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import hardcover_series_sample as series_sample  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import relaxation_label_sample as relaxation_sample  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

SCORE_SCHEMA = "description-advice-label-score-v1"
LOWER_LIMIT = 0.90
# This checkout's instruction and output-schema digests, as a candidate
# records them; read at call time so a test can stand in another checkout.
CHECKOUT_INSTRUCTION_SHA256 = hashlib.sha256(
    DESCRIPTION_INSTRUCTION.encode()
).hexdigest()
CHECKOUT_SCHEMA_SHA256 = hashlib.sha256(DESCRIPTION_OUTPUT_SCHEMA.encode()).hexdigest()

_DESCRIPTION = (
    "List Library links to the descriptions where the adviser's text could be "
    "written automatically, and score your decisions on them. Reads a copy of "
    "the application database and writes nothing."
)
_NO_SETTINGS = (
    "The copy holds no settings saved in the app, so the install reads them "
    "from config.json. Copy that file too and pass it with --config."
)
_CONFIG_NOT_NEEDED = (
    "The copy holds settings saved in the app, which the install uses instead "
    "of a configuration file. Leave out --config."
)
_CONFIG_UNREADABLE = (
    "The configuration file cannot be read. "
    "Copy /data/config.json from the install again."
)
_NO_ADVISER = (
    "No description adviser is chosen. Choose one in Settings → Reviewer, "
    "wait for Preparation idle, then copy the database again."
)
_AUTOMATIC_ON = (
    "Automatically apply ready description advice is on. Turn it off in "
    "Policy → Curation so Automatic decides none of these descriptions, then "
    "copy the database again."
)
_AUTOMATIC_ON_IN_FILE = (
    "Automatically apply ready description advice is on in config.json, so "
    "the app decides these descriptions itself. Set "
    "description_automatic_application to false, restart the service, then "
    "copy the database and the file again."
)
_ENGINES = {"codex": "Codex", "openrouter": "OpenRouter"}
_CLEANUP = (
    ("remove_marketing", "marketing"),
    ("remove_sales_boasts", "sales boasts"),
    ("remove_praise", "praise"),
)
WRONG = ("kept_current", "chose_source", "declined", "entered", "asked_again")
# The owner's outcome on a human decision, as a label; a withdrawal is none.
_OUTCOMES = {
    "keep_current": "kept_current",
    "accept": "chose_source",
    "reject": "declined",
    "owner_entered": "entered",
}

# An adviser identity: engine, model, instruction, output schema and cleanup
# digests (ADR-0099 Decision 7).
Identity = tuple[str, str, str, str, str]


def outcome_label(kind: str) -> str | None:
    """The label a latest decision of `kind` gives when its advice is L.

    `adviser` is the owner's choice of the advice; the rest are human decision
    outcomes. A withdrawal labels nothing.
    """
    if kind == "adviser":
        return "right"
    return _OUTCOMES.get(kind)


def labels_needed(right: int, total: int) -> int:
    """The fewest further right labels that lift the Wilson 95% lower limit
    to the owner's bar."""
    extra = 0
    while True:
        lower = ledger.wilson_lower_bound(right + extra, total + extra, ledger.WILSON_Z)
        if lower is not None and lower >= LOWER_LIMIT:
            return extra
        extra += 1


def _identity(candidate: DescriptionAdviceCandidate) -> Identity:
    return (
        candidate.engine,
        candidate.model,
        candidate.instruction_digest,
        candidate.schema_digest,
        candidate.preferences_digest,
    )


# The eight cleanup combinations a candidate can have been made under.
_PREFERENCES = tuple(
    DescriptionPreferences(
        remove_marketing=values[0],
        remove_sales_boasts=values[1],
        remove_praise=values[2],
    )
    for values in itertools.product((False, True), repeat=3)
)


def _preferences(digest: str) -> DescriptionPreferences | None:
    """The one cleanup combination whose digest a candidate records."""
    for preferences in _PREFERENCES:
        if description_digest(preferences.model_dump()) == digest:
            return preferences
    return None


def _description_field(report: MetadataReport) -> TargetField:
    return next(
        value
        for value in report.target.fields
        if value.destination is TargetDestination.DESCRIPTION
    )


def _stratum(current: TargetField) -> str:
    empty = {TargetFieldState.MISSING, TargetFieldState.NULL, TargetFieldState.EMPTY}
    return "fills" if current.state in empty else "replaces"


def _same_as_current(text: str, current: TargetField) -> bool:
    """The product's no-change test for an authored description write."""
    before = current.value if current.state is TargetFieldState.VALUE else None
    return values_match(TargetDestination.DESCRIPTION, before, text)


def eligible(candidate: DescriptionAdviceCandidate, current: TargetField) -> bool:
    """Whether Automatic would write this advice over `current` unasked."""
    text = candidate.output.description
    return (
        candidate.output.disposition == "ready"
        and automatic_description_floor_met(text)
        and not _same_as_current(text, current)
    )


@dataclass(slots=True)
class Group:
    """One adviser identity's labels and its fields that are not labels."""

    profiles: set[str] = field(default_factory=set)
    right: int = 0
    wrong: Counter[str] = field(default_factory=Counter)
    strata: dict[str, list[int]] = field(
        default_factory=lambda: {"replaces": [0, 0], "fills": [0, 0]}
    )
    not_counted: Counter[str] = field(default_factory=Counter)

    @property
    def labels(self) -> int:
        return self.right + sum(self.wrong.values())


@dataclass(slots=True)
class Labels:
    """Every identity group, the items with a right or wrong label, and the
    decisions that judge no advice."""

    groups: dict[Identity, Group] = field(default_factory=dict)
    labelled: set[tuple[object, ...]] = field(default_factory=set)
    withdrawn: int = 0
    decided_without_advice: int = 0

    def group(self, identity: Identity) -> Group:
        return self.groups.setdefault(identity, Group())


@dataclass(frozen=True, slots=True)
class _Decision:
    """A case's latest description decision: `adviser`, `automatic` or a
    human decision outcome."""

    case_id: str
    occurred_at: int
    kind: str
    candidate_id: str | None


class _Copy:
    """Cached reads of the copy's description history."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.candidates = {
            candidate.candidate_id: candidate
            for candidate in (
                DescriptionAdviceCandidate.from_json(document)
                for (document,) in connection.execute(
                    "SELECT document FROM description_advice_candidates"
                )
            )
        }
        self._fields: dict[str, TargetField] = {}
        self.items = ledger._case_items(
            connection,
            "rc.review_case_id IN (SELECT review_case_id FROM human_decisions "
            "WHERE destination='description' UNION SELECT review_case_id FROM "
            "current_description_decisions UNION SELECT review_case_id FROM "
            "current_review_case_lineages WHERE review_case_id IS NOT NULL)",
        )

    def item(self, case_id: str) -> tuple[object, ...]:
        return self.items.get(case_id, (case_id,))

    def report(self, case_id: str) -> MetadataReport:
        (document,) = self.connection.execute(
            "SELECT m.document FROM review_cases c JOIN metadata_reports m "
            "ON m.report_id=c.report_id WHERE c.review_case_id=?",
            (case_id,),
        ).fetchone()
        return MetadataReport.from_json(document)

    def current(self, case_id: str) -> TargetField:
        """The library's description in the case's report."""
        if case_id not in self._fields:
            self._fields[case_id] = _description_field(self.report(case_id))
        return self._fields[case_id]

    def requests(self, case_id: str) -> set[str]:
        """The request digests this checkout would send for the case's own
        report, under each cleanup combination.

        Advice reused from another case is linked to this case only through
        its current pointer, which a later preparation overwrites; the report
        is history that cannot be.
        """
        report = self.report(case_id)
        digests = set()
        for preferences in _PREFERENCES:
            try:
                inputs = DescriptionAdviceInput.from_report(report, preferences)
            except ValueError:
                continue
            digests.add(
                ContainedReviewRequest(
                    DESCRIPTION_INSTRUCTION,
                    description_document(inputs.model_dump()),
                    DESCRIPTION_OUTPUT_SCHEMA,
                ).digest
            )
        return digests

    def offered(self, case_id: str) -> set[str]:
        """The request identities whose advice the case could have shown."""
        return {
            request_identity
            for (request_identity,) in self.connection.execute(
                "SELECT a.request_identity FROM current_description_advice p "
                "JOIN description_advice_attempts a ON a.attempt_id=p.attempt_id "
                "WHERE p.review_case_id=? UNION SELECT request_identity FROM "
                "description_advice_attempts "
                "WHERE json_extract(binding_document,'$.review_case_id')=?",
                (case_id, case_id),
            )
        }


def _case_decisions(connection: sqlite3.Connection) -> list[_Decision]:
    """Each case's latest description decision (ADR-0099 Decision 2).

    A current authored selection is the latest where it exists, since a later
    human decision on the description removes it; otherwise the latest human
    decision on the case by ordinal, over every episode: the label reads
    history, not what the case's current episode still holds. A pin or an
    unpin is never the latest decision, so the one before it stands.
    """
    latest: dict[str, _Decision] = {}
    for case_id, outcome, occurred_at in connection.execute(
        "SELECT human.review_case_id,human.outcome,human.occurred_at "
        "FROM human_decisions human WHERE human.destination='description' "
        "AND human.outcome NOT IN ('pin','unpin') ORDER BY human.ordinal"
    ):
        latest[case_id] = _Decision(case_id, occurred_at, outcome, None)
    for case_id, document in connection.execute(
        "SELECT current.review_case_id,decision.document "
        "FROM current_description_decisions current JOIN owner_authored_descriptions "
        "decision ON decision.decision_id=current.decision_id UNION ALL "
        "SELECT current.review_case_id,decision.document "
        "FROM current_description_decisions current JOIN automatic_description_decisions "
        "decision ON decision.decision_id=current.decision_id"
    ):
        authored = AuthoredDescriptionDecision.from_json(document)
        latest[case_id] = _Decision(
            case_id,
            authored.occurred_at,
            "adviser" if authored.origin == "human" else "automatic",
            authored.candidate_id,
        )
    return list(latest.values())


def _displayed(copy: _Copy, decision: _Decision) -> DescriptionAdviceCandidate | None:
    """The advice D the decision judged (ADR-0099 Decision 3)."""
    if decision.candidate_id is not None:
        return copy.candidates[decision.candidate_id]
    offered = copy.offered(decision.case_id)
    requests = copy.requests(decision.case_id)
    shown = [
        candidate
        for candidate in copy.candidates.values()
        if (
            candidate.request_identity in offered
            or candidate.request_digest in requests
        )
        and candidate.created_at <= decision.occurred_at
    ]
    return max(
        shown,
        key=lambda candidate: (candidate.created_at, candidate.candidate_id),
        default=None,
    )


def _first(
    displayed: DescriptionAdviceCandidate, copy: _Copy, decision: _Decision
) -> DescriptionAdviceCandidate:
    """L: the earliest advice of D's request, the answer Automatic would have
    acted on before any retry. D itself qualifies, so there is always one."""
    return min(
        (
            candidate
            for candidate in copy.candidates.values()
            if candidate.request_identity == displayed.request_identity
            and candidate.created_at <= decision.occurred_at
        ),
        key=lambda candidate: (candidate.created_at, candidate.candidate_id),
        default=displayed,
    )


def _label(copy: _Copy, labels: Labels, decision: _Decision) -> None:
    """Count one item's latest decision under the ADR-0099 label rule."""
    if decision.kind == "withdraw":
        labels.withdrawn += 1
        return
    displayed = _displayed(copy, decision)
    if displayed is None:
        labels.decided_without_advice += 1
        return
    group = labels.group(_identity(displayed))
    if decision.kind == "automatic":
        group.profiles.add(displayed.profile_digest)
        group.not_counted["decided_by_automatic"] += 1
        return
    first = _first(displayed, copy, decision)
    if not eligible(first, copy.current(decision.case_id)):
        # Automatic would never have written it unasked (ADR-0099 Decision 3).
        group.profiles.add(first.profile_digest)
        group.not_counted["not_eligible"] += 1
        return
    group.profiles.add(first.profile_digest)
    label = (
        "asked_again"
        if displayed.candidate_id != first.candidate_id
        else outcome_label(decision.kind)
    )
    if label is None:  # pragma: no cover - every recorded outcome maps
        return
    stratum = group.strata[_stratum(copy.current(decision.case_id))]
    if label == "right":
        group.right += 1
        stratum[0] += 1
    else:
        group.wrong[label] += 1
        stratum[1] += 1
    labels.labelled.add(copy.item(decision.case_id))


def read_labels(copy: _Copy) -> Labels:
    """One label per library item: its latest description decision across
    its cases, ties to the case id."""
    winners: dict[tuple[object, ...], _Decision] = {}
    for decision in _case_decisions(copy.connection):
        key = copy.item(decision.case_id)
        held = winners.get(key)
        if held is None or (held.occurred_at, held.case_id) < (
            decision.occurred_at,
            decision.case_id,
        ):
            winners[key] = decision
    labels = Labels()
    for decision in winners.values():
        _label(copy, labels, decision)
    return labels


def _current_advice(
    copy: _Copy,
) -> list[tuple[str, DescriptionAdviceBinding, DescriptionAdviceCandidate | None]]:
    """Each current case's bound advice, in case order."""
    return [
        (
            case_id,
            DescriptionAdviceBinding.model_validate_json(binding),
            None if candidate_id is None else copy.candidates[candidate_id],
        )
        for case_id, binding, candidate_id in copy.connection.execute(
            "SELECT p.review_case_id,p.binding_document,p.candidate_id "
            "FROM current_description_advice p JOIN current_review_case_lineages l "
            "ON l.review_case_id=p.review_case_id ORDER BY p.review_case_id"
        )
    ]


def _count_undecided(
    copy: _Copy, labels: Labels, reader: SQLiteMetadataReportRepository
) -> None:
    """Current fields whose bound, current advice is eligible and whose item
    has no label."""
    store = reader.description_advice_store()
    for case_id, binding, candidate in _current_advice(copy):
        if (
            candidate is not None
            and copy.item(case_id) not in labels.labelled
            and eligible(candidate, copy.current(case_id))
            and store.binding_is_current(binding)
        ):
            group = labels.group(_identity(candidate))
            group.profiles.add(candidate.profile_digest)
            group.not_counted["undecided"] += 1


def _lower(right: int, total: int) -> float | None:
    return ledger.wilson_lower_bound(right, total, ledger.WILSON_Z)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _group_document(identity: Identity, group: Group) -> dict[str, object]:
    engine, model, instruction, schema, preferences_digest = identity
    this_checkout = (instruction, schema) == (
        CHECKOUT_INSTRUCTION_SHA256,
        CHECKOUT_SCHEMA_SHA256,
    )
    preferences = _preferences(preferences_digest)
    lower = _lower(group.right, group.labels)
    return {
        "engine": engine,
        "model": model,
        "instruction_sha256": instruction,
        "output_schema_sha256": schema,
        "this_checkout": this_checkout,
        "preferences": None if preferences is None else preferences.model_dump(),
        "preferences_digest": preferences_digest,
        "qualification_manifest_sha256": (
            description_qualification_manifest(preferences)
            if this_checkout and preferences is not None
            else None
        ),
        "profile_digests": sorted(group.profiles),
        "labels": group.labels,
        "right": group.right,
        "wrong": {name: group.wrong[name] for name in WRONG},
        "strata": {
            name: {
                "right": right,
                "wrong": wrong,
                "wilson_lower_limit": _rounded(_lower(right, right + wrong)),
            }
            for name, (right, wrong) in group.strata.items()
        },
        "wilson_lower_limit": _rounded(lower),
        "labels_needed": labels_needed(group.right, group.labels),
        "meets_bar": group.labels > 0 and lower is not None and lower >= LOWER_LIMIT,
        "not_counted": {
            name: group.not_counted[name]
            for name in ("not_eligible", "decided_by_automatic", "undecided")
        },
    }


def score_document(labels: Labels) -> dict[str, object]:
    """The shareable score: counts, digests, engine and model only."""
    checkout = (CHECKOUT_INSTRUCTION_SHA256, CHECKOUT_SCHEMA_SHA256)
    ordered = sorted(
        labels.groups.items(),
        key=lambda pair: (
            (pair[0][2:4] != checkout, pair[0][0], pair[0][1], pair[0][4])
            + pair[0][2:4]
        ),
    )
    return {
        "schema_version": SCORE_SCHEMA,
        "bar": {
            "wilson_lower_limit": LOWER_LIMIT,
            "confidence": 0.95,
            "set_by": "owner",
            "decision": "ADR-0099",
        },
        "checkout": {
            "instruction_sha256": CHECKOUT_INSTRUCTION_SHA256,
            "output_schema_sha256": CHECKOUT_SCHEMA_SHA256,
        },
        "identities": [_group_document(identity, group) for identity, group in ordered],
        "not_counted": {
            "withdrawn": labels.withdrawn,
            "decided_without_advice": labels.decided_without_advice,
        },
    }


def _cleanup_line(preferences: object) -> str:
    if not isinstance(preferences, dict):
        return "Cleanup choices unknown."
    removed = [name for key, name in _CLEANUP if preferences[key]]
    if not removed:
        return "Cleanup removes nothing."
    listed = (
        removed[0]
        if len(removed) == 1
        else ", ".join(removed[:-1]) + " and " + removed[-1]
    )
    return f"Cleanup removes {listed}."


def _group_text(group: dict[str, object]) -> list[str]:
    wrong = group["wrong"]
    strata = group["strata"]
    not_counted = group["not_counted"]
    assert isinstance(wrong, dict)
    assert isinstance(strata, dict)
    assert isinstance(not_counted, dict)
    instructions = (
        "this checkout's instructions"
        if group["this_checkout"]
        else "earlier instructions"
    )
    replaces, fills = strata["replaces"], strata["fills"]
    lower = group["wilson_lower_limit"]
    return [
        f"Description adviser: {_ENGINES[str(group['engine'])]}, {group['model']}, "
        f"with {instructions}.",
        _cleanup_line(group["preferences"]),
        f"Labels: {group['labels']}, {group['right']} right and "
        f"{sum(wrong.values())} wrong.",
        f"Wrong labels: {wrong['kept_current']} kept the current description, "
        f"{wrong['chose_source']} chose a source's description, "
        f"{wrong['declined']} declined the change, {wrong['entered']} entered "
        f"your own, {wrong['asked_again']} asked for new advice.",
        f"Replacing a description: {replaces['right']} right and "
        f"{replaces['wrong']} wrong. Filling an empty one: {fills['right']} right "
        f"and {fills['wrong']} wrong.",
        "Wilson 95% lower limit: none yet."
        if lower is None
        else f"Wilson 95% lower limit: {lower:.4f}.",
        "This meets the bar of 0.90."
        if group["meets_bar"]
        else "Labels still needed for the bar: "
        f"{group['labels_needed']}, if each is right.",
        f"Not counted: {not_counted['not_eligible']} not eligible, "
        f"{not_counted['decided_by_automatic']} decided by Automatic, "
        f"{not_counted['undecided']} undecided.",
    ]


def render_score(document: dict[str, object]) -> str:
    identities = document["identities"]
    not_counted = document["not_counted"]
    assert isinstance(identities, list)
    assert isinstance(not_counted, dict)
    lines: list[str] = []
    for group in identities:
        if lines:
            lines.append("")
        lines.extend(_group_text(group))
    if not lines:
        lines.append(
            "No labels yet. Decide the descriptions sample lists, then score a "
            "new copy."
        )
    lines += [
        "",
        f"Also not counted: {not_counted['withdrawn']} withdrawn, "
        f"{not_counted['decided_without_advice']} decided before any advice.",
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class Adviser:
    """The chosen description adviser: its engine, its model where known, and
    the identity check `sample` binds to the store."""

    engine: str
    model: str | None
    profile: tuple[str, str] | None


def _adviser(copy: _Copy, access: ReviewerAccessDocument) -> Adviser:
    selection = access.description_rewrite
    assert selection is not None
    if isinstance(selection, OpenRouterTaskSelection):
        model = selection.model
        return Adviser(
            "openrouter",
            model,
            (model, hashlib.sha256(openrouter_profile(model)).hexdigest()),
        )
    codex = sorted(
        (
            candidate
            for candidate in copy.candidates.values()
            if candidate.engine == "codex"
        ),
        key=lambda candidate: (candidate.created_at, candidate.candidate_id),
    )
    current = [
        candidate for candidate in codex if candidate.access_revision == access.revision
    ]
    return Adviser(
        "codex",
        codex[-1].model if codex else None,
        (current[-1].model, current[-1].profile_digest) if current else None,
    )


def _refuse_sample(copy: _Copy, reader: SQLiteMetadataReportRepository) -> Adviser:
    """Refuse a copy `sample` cannot list from, in the S8 order, and bind
    the adviser's identity check."""
    connection = copy.connection
    newest = max(
        copy.candidates.values(),
        key=lambda candidate: (candidate.created_at, candidate.candidate_id),
        default=None,
    )
    if newest is not None and (newest.instruction_digest, newest.schema_digest) != (
        CHECKOUT_INSTRUCTION_SHA256,
        CHECKOUT_SCHEMA_SHA256,
    ):
        raise SystemExit(relaxation_sample._ENGINE)
    row = connection.execute(
        "SELECT document FROM reviewer_access WHERE singleton=1"
    ).fetchone()
    access = None if row is None else ReviewerAccessDocument.model_validate_json(row[0])
    if access is None or access.description_rewrite is None:
        raise SystemExit(_NO_ADVISER)
    store = reader.description_advice_store()
    if store.current_settings().automatic_application:
        raise SystemExit(_AUTOMATIC_ON if _saved(copy) else _AUTOMATIC_ON_IN_FILE)
    adviser = _adviser(copy, access)

    def identity(engine: str, model: str | None) -> tuple[str, str] | None:
        if engine == "openrouter" and model is not None:
            return model, hashlib.sha256(openrouter_profile(model)).hexdigest()
        return adviser.profile if engine == "codex" else None

    store.bind_identity(identity)
    return adviser


@dataclass(slots=True)
class Sample:
    """`sample`'s buckets, its links with their strata, and this adviser's
    labels."""

    adviser: Adviser
    links: list[tuple[str, str]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    right: int = 0
    labels: int = 0


def _choosable(
    reader: SQLiteMetadataReportRepository, case_id: str, candidate_id: str
) -> bool | None:
    """Whether the work's page offers this advice and Keep current; None when
    the page cannot be read."""
    try:
        detail = InspectCurrentReviewCase(reader).run(case_id)
        fields = detail["fields"]
        assert isinstance(fields, list)
        description = next(
            (value for value in fields if value["destination"] == "description"),
            None,
        )
    except Exception:
        return None
    if description is None:
        return False
    advice = description.get("description_advice")
    return (
        description.get("editable") is True
        and "keep_current" in description.get("decision_actions", ())
        and isinstance(advice, dict)
        and advice.get("current") is True
        and isinstance(advice.get("candidate"), dict)
        and advice["candidate"].get("candidate_id") == candidate_id
    )


def _bucket(
    copy: _Copy,
    reader: SQLiteMetadataReportRepository,
    labels: Labels,
    case_id: str,
) -> str:
    """One current case's bucket, in the S10 order."""
    store = reader.description_advice_store()
    advice = store.read(case_id)
    if advice is None:
        try:
            DescriptionAdviceInput.from_report(
                copy.report(case_id), store.current_settings().cleanup()
            )
        except ValueError:
            return "no_source"
        return "waiting"
    if advice["state"] in {"requested", "running"}:
        return "waiting"
    if advice["state"] in {"failed", "interrupted"}:
        return "failed"
    document = advice["candidate"]
    if document is None or advice["current"] is not True:
        return "out_of_date"
    candidate = DescriptionAdviceCandidate.model_validate(document)
    current = copy.current(case_id)
    if candidate.output.disposition != "ready":
        return "needs_review"
    if not automatic_description_floor_met(candidate.output.description):
        return "below_floor"
    if _same_as_current(candidate.output.description, current):
        return "same"
    if copy.item(case_id) in labels.labelled:
        return "decided"
    choosable = _choosable(reader, case_id, candidate.candidate_id)
    if choosable is None:
        return "unchecked"
    return _stratum(current) if choosable else "cannot_choose"


def read_sample(copy: _Copy, reader: SQLiteMetadataReportRepository) -> Sample:
    adviser = _refuse_sample(copy, reader)
    labels = read_labels(copy)
    sample = Sample(adviser)
    for (case_id,) in copy.connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages "
        "WHERE review_case_id IS NOT NULL ORDER BY review_case_id"
    ).fetchall():
        bucket = _bucket(copy, reader, labels, case_id)
        sample.counts[bucket] += 1
        if bucket in {"replaces", "fills"}:
            sample.links.append((case_id, bucket))
    if adviser.model is not None:
        preferences = reader.description_advice_store().current_settings().cleanup()
        group = labels.groups.get(
            (
                adviser.engine,
                adviser.model,
                CHECKOUT_INSTRUCTION_SHA256,
                CHECKOUT_SCHEMA_SHA256,
                description_digest(preferences.model_dump()),
            )
        )
        if group is not None:
            sample.right, sample.labels = group.right, group.labels
    return sample


def render_sample(sample: Sample, origin: str, limit: int | None) -> str:
    counts = sample.counts
    adviser = sample.adviser
    needed = labels_needed(sample.right, sample.labels)
    lower = _lower(sample.right, sample.labels)
    met = sample.labels > 0 and lower is not None and lower >= LOWER_LIMIT
    lines = [
        f"Description adviser: {_ENGINES[adviser.engine]}."
        if adviser.model is None
        else f"Description adviser: {_ENGINES[adviser.engine]}, {adviser.model}.",
        f"To decide: {len(sample.links)}, {counts['replaces']} replacing a "
        f"description and {counts['fills']} filling an empty one.",
        f"Already decided: {counts['decided']}.",
        f"Advice you cannot choose on the work's page: {counts['cannot_choose']}.",
        f"Advice the adviser marked as needing you: {counts['needs_review']}.",
        f"Advice below the description floor: {counts['below_floor']}.",
        f"Advice the same as the current description: {counts['same']}.",
        f"Advice out of date: {counts['out_of_date']}.",
        f"Waiting for advice: {counts['waiting']}.",
        f"Advice request failed: {counts['failed']}.",
        f"No source description to rewrite: {counts['no_source']}.",
        f"Labels for this adviser: {sample.labels}, {sample.right} choosing its "
        "description.",
        "Your labels meet the bar. Score a new copy."
        if met
        else f"Labels still needed for the bar: {needed}, if each is right.",
    ]
    if limit is not None or not met:
        shown = sample.links[: needed if limit is None else limit]
        lines.extend(
            f"{origin}/library?lens=all&preview={case_id}&field=description"
            for case_id, _stratum_name in shown
        )
        if len(sample.links) > len(shown):
            lines.append(f"Showing {len(shown)} of {len(sample.links)}.")
    unchecked = counts["unchecked"]
    if unchecked == 1:
        lines.append("1 work could not be checked and is left out.")
    elif unchecked > 1:
        lines.append(f"{unchecked} works could not be checked and are left out.")
    return "\n".join(lines) + "\n"


def _saved(copy: _Copy) -> bool:
    """Whether the copy's settings are saved in the app (store mode)."""
    return (
        copy.connection.execute(
            "SELECT 1 FROM owner_settings WHERE singleton=1"
        ).fetchone()
        is not None
    )


def _configured(path: Path) -> DescriptionAdviceSettings:
    """The description choices the app binds from a configuration file at
    start; the defaults when it has no review section."""
    try:
        review = load_configuration_file(path).review
    except Exception as error:
        raise SystemExit(_CONFIG_UNREADABLE) from error
    if review is None:
        return DescriptionAdviceSettings()
    return DescriptionAdviceSettings(
        remove_marketing=review.description_remove_marketing,
        remove_sales_boasts=review.description_remove_sales_boasts,
        remove_praise=review.description_remove_praise,
        automatic_application=review.description_automatic_application,
    )


def _bind(
    copy: _Copy,
    reader: SQLiteMetadataReportRepository,
    config: Path | None,
    required: bool,
) -> None:
    """Read the copy under the settings its install uses: saved in the app,
    or bound from the configuration file."""
    if _saved(copy):
        if config is not None:
            raise SystemExit(_CONFIG_NOT_NEEDED)
    elif config is not None:
        reader.description_advice_store().bind_settings(_configured(config))
    elif required:
        raise SystemExit(_NO_SETTINGS)


def _open(database: Path) -> SQLiteShadowPreviewRepository:
    if not database.is_file():
        raise SystemExit(series_sample._MISSING)
    wal = database.with_name(database.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise SystemExit(relaxation_sample._WAL)
    try:
        return open_shadow_preview_repository(database)
    except Exception as error:
        refusal = database_copy.unicode_refusal(database)
        raise SystemExit(refusal or series_sample._UNREADABLE) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample", help="list the descriptions to decide")
    score = commands.add_parser("score", help="count your decisions")
    for command in (sample, score):
        command.add_argument(
            "--database",
            required=True,
            type=Path,
            help="a copy of application.sqlite3",
        )
        command.add_argument(
            "--config",
            default=None,
            type=Path,
            help="a copy of config.json, for an install whose settings come from it",
        )
    sample.add_argument(
        "--origin",
        default=series_sample.DEFAULT_ORIGIN,
        type=series_sample._origin,
        help=(
            "the address you open Mechanical Librarian at "
            "(default http://localhost:8080)"
        ),
    )
    sample.add_argument(
        "--limit",
        default=None,
        type=series_sample._limit,
        help=("how many links to print (default: as many as the bar still needs)"),
    )
    score.add_argument(
        "--format",
        default="text",
        choices=("text", "json"),
        help="text to read, or json to share (default text)",
    )
    return parser


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    arguments = _parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    reader = _open(arguments.database)
    try:
        copy = _Copy(reader._reader._connection)
        # Labels need no settings, so score runs without them; the count of
        # undecided advice then assumes the default choices.
        _bind(copy, reader._reader, arguments.config, arguments.command == "sample")
        if arguments.command == "sample":
            text = render_sample(
                read_sample(copy, reader._reader), arguments.origin, arguments.limit
            )
        else:
            labels = read_labels(copy)
            _count_undecided(copy, labels, reader._reader)
            document = score_document(labels)
            text = (
                canonical_json(document) + "\n"
                if arguments.format == "json"
                else render_score(document)
            )
    except Exception as error:
        # Anything this checkout cannot read in the copy, including a damaged
        # document whose validation error would carry library text.
        raise SystemExit(series_sample._UNREADABLE) from error
    finally:
        reader.close()
    output.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
