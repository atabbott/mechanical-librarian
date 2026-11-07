"""Aggregate-only replay of the early admission and exclusion gates (#1350).

A workbench instrument, deliberately not part of the installed product: a
maintainer runs it by hand from a checkout, offline, against COPIES of (or
read-only access to) a retained snapshot's ``application.sqlite3``. It writes
nothing and prints one ``admission-rule-ledger-v1`` JSON document, or the
ledger's Markdown tables, to stdout.

For every gate in the committed catalogue it decides, per snapshot, whether the
gate can be measured there. A gate whose introducing commit is not an ancestor
of the snapshot's source revision is ``gate_postdates_source``; one whose
refused input that source never persisted is ``block_not_retained``. For every
measurable gate it counts the fields and works the gate's persisted signal
blocked (per gate and non-exclusive, so each count is a ceiling), and the
identity precision of loosening the gate, from natural controls: provider
records the report itself bound to the work (positives) or to another work
(negatives). Each precision carries the lower limit of a two-sided 95% Wilson
interval. Rows rank by actionable fields (blocked fields with something the
loosened rule could admit) times that lower limit.

Every decision-level (alternative-control) row also splits its controls and
its pick measure by destination and by medium (#1744), and classes its
actionable fields: tied, decidable, contaminated, no right-work alternative or
single alternative. ``--breakdown GATE`` renders one such row's split as an
extra Markdown section. Where the snapshot stores automatic decisions, those
rows also count the blocked fields one already settled (#1747).

Attention per case (#1310) is read under each snapshot's own source: a child
``python -I`` process imports only that revision's tree and composes the
per-case query from its own ``review_sql`` fragments, so no attention
predicate is copied here.

The output is aggregate-only by construction: every string in it is a
validated snapshot label, a source revision, a catalogue string written in this
file, or a member of the product's closed destination and reason vocabularies;
anything else raises before a byte is written. No title, creator, identifier,
path, configured connection, library or provider name is ever emitted.

The ``--a6-abstention`` flag adds the automatic-decision abstention analysis
of #1745 (``scripts/admission_rule_a6.py``) to the JSON document under
``a6_abstention``: its two populations, the first failing eligibility check
and the closed relaxations, replayed under each source. With ``--format
markdown`` it prints that analysis's tables instead of the ledger's, so each
page is one run's output.

The ``--a3-combination`` flag adds the bibliographic-combination analysis of
#1746 (``scripts/admission_rule_a3.py``) under ``a3_combination``: each A3
field's outcome before the gate overwrote it, the containment clause that
vetoed each combination, a re-check under main's same-name series rule and
three closed relaxations, replayed under each source. With ``--format
markdown`` it prints that analysis's tables instead of the ledger's.

Usage:
    git archive --format=tar --prefix=src-REV/ REV src scripts | tar -x -C SCRATCH
    uv run python scripts/admission_rule_ledger.py \
        --snapshot LABEL /copy/application.sqlite3 REV \
        --source-root LABEL SCRATCH/src-REV \
        [--source-report LABEL /copy/cold-start-report.json] \
        [--snapshot ...] [--repository CHECKOUT] [--format json|markdown] \
        [--follow-on GATE=ISSUE ...] [--breakdown GATE] \
        [--a6-abstention | --a3-combination]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Literal, NoReturn, TextIO, cast
from urllib.parse import quote

from mechanical_librarian.domain import shadow_decision, shadow_outcome
from mechanical_librarian.domain.metadata_report import TargetDestination, isbn_family
from mechanical_librarian.domain.proposal_set import _OUTCOME_REASON_SETS

if TYPE_CHECKING:
    from scripts.admission_rule_a3 import A3Reader
    from scripts.admission_rule_a6 import A6Reader

LEDGER_SCHEMA = "admission-rule-ledger-v2"
WILSON_Z = 1.959964
# Mirrors of product bounds the replay compares against; a unit test pins each
# to the product constant so neither can drift silently.
MAX_ISBN_DISCOVERY_LANES = 8

_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_GATE_ID = re.compile(r"^[A-Z][0-9]{1,2}[a-z]?$")

Status = Literal[
    "measurable",
    "gate_postdates_source",
    "block_not_retained",
    "cause_not_separable",
    "rich_refusals_collapsed",
]
Control = Literal[
    "alternative", "cover_candidates", "not_identity", "not_retained", "informational"
]
Ancestry = Callable[[str, str], bool]

_UNBLOCKED_OUTCOMES = frozenset({"propose_change", "acceptable_choice", "keep"})
_POSITIVE_RELATIONS = frozenset({"accepted", "associated", "recording_associated"})
_CONTRADICTION_EVIDENCE = "edition_work_relation_contradiction"
_REFUSING_DISPOSITIONS = frozenset({"abstention", "manual", "conflict", "unsupported"})
_EPUB_INAPPLICABLE = frozenset({"narrators", "explicit"})
_DESTINATIONS = tuple(value.value for value in TargetDestination)
_ALL = frozenset(_DESTINATIONS)
_SAFETY_CAUSE = "automatic_safety_suspended"
_OTHER_REASON = "other_reason"
_MEDIA = ("audiobook", "ebook")
_REQUIRED_TABLES = frozenset(
    {
        "current_review_packages",
        "review_cases",
        "metadata_reports",
        "shadow_decision_outcomes",
        "cover_considerations",
        "current_cover_considerations",
    }
)
_CLAIM_DESTINATIONS: dict[str, frozenset[str]] = {
    "title": frozenset({"title"}),
    "subtitle": frozenset({"subtitle"}),
    "creator": frozenset({"authors"}),
    "contributor": frozenset({"narrators"}),
    "series": frozenset({"series"}),
    "series_sequence": frozenset({"series"}),
    "description": frozenset({"description"}),
    "publisher": frozenset({"publisher"}),
    "imprint": frozenset({"publisher"}),
    "publication_date": frozenset({"publishedDate", "publishedYear"}),
    "date_event": frozenset({"publishedDate", "publishedYear"}),
    "isbn": frozenset({"isbn"}),
    "asin": frozenset({"asin"}),
    "language": frozenset({"language"}),
    "subject": frozenset({"genres", "tags"}),
    "genre": frozenset({"genres"}),
    "tag": frozenset({"tags"}),
}
_BOUNDED_STATES = frozenset({"oversized", "unsupported"})
_COVER_SOURCE_REFUSALS = frozenset(
    {"rejected", "truncated", "oversized", "unsupported_format", "invalid_declaration"}
)
_PREVIEW_CAP_OMISSIONS = frozenset({"shortlist_limit", "aggregate_byte_limit"})


class Refusal(Exception):
    """A fixed refusal; the message never carries a path, value or exception text."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# JSON accessors: report documents are read as untyped JSON, in memory only.


def _dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _dicts(value: object) -> list[dict[str, object]]:
    return [_dict(item) for item in _list(value) if isinstance(item, dict)]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _document(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            return _dict(json.loads(value))
        except ValueError:
            return {}
    return _dict(value)


# ---------------------------------------------------------------------------
# One current review case, as the signals read it.


@dataclass(slots=True)
class Case:
    medium: str
    epub: bool
    report: dict[str, object]
    results: dict[str, dict[str, object]]
    shadow: dict[str, set[tuple[str, str]]]
    cover_candidates: list[dict[str, object]]
    cover_omissions: set[str]
    # cover identity candidate reference -> the provider records it pictures
    cover_records: dict[str, frozenset[str]] = field(default_factory=dict)
    # report cover source id -> the provider records its provenance binds
    cover_sources: dict[str, frozenset[str]] = field(default_factory=dict)
    # provider record identity -> (provider ordinal, work identity, contradicted)
    records: dict[str, tuple[int, str, bool]] = field(default_factory=dict)
    references: dict[str, str] = field(default_factory=dict)
    lane_kinds: dict[str, set[str]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    # destination -> (outcome, candidate) of the snapshot's automatic decision
    automatic: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    # The source revision's scalar decision destinations (#1745), as its own
    # shadow_decision._SUPPORTED_DESTINATIONS states them.
    scalar: frozenset[str] = frozenset()

    def applicable(self, destinations: frozenset[str]) -> frozenset[str]:
        present = destinations & frozenset(self.results)
        return present - _EPUB_INAPPLICABLE if self.epub else present

    def reasons(self, destination: str) -> frozenset[str]:
        result = self.results.get(destination, {})
        stated = {
            value for value in _list(result.get("reasons")) if isinstance(value, str)
        }
        refused = {
            code
            for disposition, code in self.shadow.get(destination, set())
            if disposition in _REFUSING_DISPOSITIONS
        }
        return frozenset(stated | refused)

    def blocked(self, destination: str) -> bool:
        outcome = _text(self.results.get(destination, {}).get("outcome"))
        return outcome is not None and outcome not in _UNBLOCKED_OUTCOMES

    def providers(self) -> list[dict[str, object]]:
        return _dicts(self.report.get("providers"))

    def lanes(self) -> list[dict[str, object]]:
        return [
            lane
            for provider in self.providers()
            for lane in _dicts(provider.get("lanes"))
        ]

    def diagnostics(self) -> list[dict[str, object]]:
        return [
            diagnostic
            for owner in (*self.providers(), *self.lanes())
            for diagnostic in _dicts(owner.get("diagnostics"))
        ]

    def local_claims(self) -> list[dict[str, object]]:
        return _dicts(_dict(self.report.get("local")).get("claims"))

    def identifier_evidence(self) -> list[dict[str, object]]:
        return _dicts(self.report.get("identifier_evidence"))

    def resolve(self, references: Iterable[object]) -> frozenset[str]:
        found: set[str] = set()
        for reference in references:
            if not isinstance(reference, str):
                continue
            if reference in self.records:
                found.add(reference)
            elif reference in self.references:
                found.add(self.references[reference])
            elif reference in self.cover_sources:
                # A cover alternative cites its report cover sources.
                found |= self.cover_sources[reference]
        return frozenset(found)


def _build_case(
    case_document: dict[str, object],
    report: dict[str, object],
    shadow_document: dict[str, object],
    consideration: dict[str, object],
) -> Case:
    results: dict[str, dict[str, object]] = {}
    for result in _dicts(case_document.get("results")):
        destination = _text(result.get("destination"))
        if destination is not None:
            results[destination] = result
    shadow: dict[str, set[tuple[str, str]]] = {}
    for outcome in _dicts(shadow_document.get("outcomes")):
        destination = _text(outcome.get("destination"))
        disposition = _text(outcome.get("disposition"))
        code = _text(outcome.get("reason_code"))
        if destination and disposition and code:
            shadow.setdefault(destination, set()).add((disposition, code))
    identity = _document(consideration.get("identity_document"))
    packet = _document(consideration.get("packet_document"))
    omissions = {
        reason
        for owner in (consideration, packet)
        for omission in _dicts(owner.get("omissions"))
        if (reason := _text(omission.get("reason"))) is not None
    }
    file_facts = _dict(report.get("file"))
    case = Case(
        medium="ebook" if report.get("audio") is None else "audiobook",
        epub=file_facts.get("format") == "epub",
        report=report,
        results=results,
        shadow=shadow,
        cover_candidates=_dicts(identity.get("candidates")),
        cover_omissions=omissions,
    )
    _index_records(case)
    _index_cover_records(case, consideration)
    return case


def _index_cover_records(case: Case, consideration: dict[str, object]) -> None:
    """Bind each cover candidate to the provider records whose covers it shows.

    A cover's provenance binds the records the source revision's
    ``_classify_sources`` matches: same provider and work identity, narrowed to
    the stated edition when there is one. A work-level cover (a default, work
    or related cover without an edition) binds every record of that work at
    that provider.
    """
    works: dict[tuple[str, str], list[str]] = {}
    for provider in case.providers():
        name = _text(provider.get("provider"))
        if name is None:
            continue
        for candidate in _dicts(provider.get("candidates")):
            identity = _text(candidate.get("identity"))
            work = _text(candidate.get("work_identity"))
            if identity is not None and work is not None and identity in case.records:
                works.setdefault((name, work), []).append(identity)
    editions: dict[str, set[str]] = {}
    for cover in _dicts(case.report.get("covers")):
        artifact = _text(_dict(cover.get("artifact")).get("artifact_id"))
        provenance = _dict(cover.get("provenance"))
        name = _text(provenance.get("provider"))
        work = _text(provenance.get("work_identity"))
        if artifact is None or name is None or work is None:
            continue
        edition = _text(provenance.get("edition_identity"))
        bound = {
            identity
            for identity in works.get((name, work), [])
            if edition is None or identity == edition
        }
        source = _text(cover.get("source_id"))
        if source is not None:
            case.cover_sources[source] = frozenset(bound)
        if bound:
            editions.setdefault(artifact, set()).update(bound)
    for candidate in _dicts(consideration.get("candidates")):
        reference = _text(candidate.get("reference"))
        artifact = _text(candidate.get("artifact_id"))
        if reference is not None and artifact is not None:
            case.cover_records[reference] = frozenset(editions.get(artifact, set()))


# Providers without a work authority (Google Books, Audnexus and others) state
# this sentinel; it names no work, so the source's identifier evidence never
# treats it as one (derive_identifier_evidence).
_UNRESOLVED_WORK = "unresolved"


def _work(value: object) -> str:
    """A record's work identity for control labelling; "" when it names none."""
    work = _text(value)
    return "" if work is None or work == _UNRESOLVED_WORK else work


def _index_records(case: Case) -> None:
    """Index provider records and label each one from the report's own bindings."""
    names: dict[str, int] = {}
    for ordinal, provider in enumerate(case.providers()):
        name = _text(provider.get("provider"))
        if name is not None:
            names.setdefault(name, ordinal)
        for candidate in _dicts(provider.get("candidates")):
            identity = _text(candidate.get("identity"))
            if identity is None:
                continue
            contradicted = any(
                evidence.get("kind") == _CONTRADICTION_EVIDENCE
                for evidence in _dicts(candidate.get("evidence"))
            )
            case.records[identity] = (
                ordinal,
                _work(candidate.get("work_identity")),
                contradicted,
            )
            for claim in _dicts(candidate.get("claims")):
                reference = _text(claim.get("reference"))
                if reference is not None:
                    case.references[reference] = identity
        for lane in _dicts(provider.get("lanes")):
            kind = _text(lane.get("kind")) or ""
            for candidate in _dicts(lane.get("candidates")):
                edition = _text(candidate.get("edition_identity"))
                if edition is not None:
                    case.lane_kinds.setdefault(edition, set()).add(kind)
    positive: set[str] = set()
    unrelated: set[str] = set()
    positive_works: dict[int, set[str]] = {}
    for evidence in case.identifier_evidence():
        bound = _text(evidence.get("candidate_identity"))
        bound = bound if bound is not None and bound in case.records else None
        reference = _text(evidence.get("reference"))
        if reference is not None and bound is not None:
            case.references[reference] = bound
        relation = _text(evidence.get("relation"))
        if relation in _POSITIVE_RELATIONS:
            if bound is not None:
                positive.add(bound)
            owner = names.get(_text(evidence.get("provider")) or "")
            work = _work(evidence.get("work_identity"))
            if owner is not None and work:
                positive_works.setdefault(owner, set()).add(work)
        elif relation == "unrelated" and bound is not None:
            unrelated.add(bound)
    for identity in positive:
        ordinal, work, _ = case.records[identity]
        if work:
            positive_works.setdefault(ordinal, set()).add(work)
    for identity, (ordinal, work, contradicted) in case.records.items():
        works = positive_works.get(ordinal, set())
        if identity in unrelated or contradicted:
            label = "negative"
        elif identity in positive or (work and work in works):
            label = "positive"
        elif works and work:
            label = "negative"
        else:
            label = "unlabelled"
        case.labels[identity] = label


# ---------------------------------------------------------------------------
# Signals. Each returns the destinations its persisted signal is on; the
# blocked test (outcome) and medium applicability are applied by the counter.

Signal = Callable[[Case], frozenset[str]]
CoverPredicate = Callable[[Case, dict[str, object]], bool]


def _reason(codes: Iterable[str], destinations: frozenset[str] = _ALL) -> Signal:
    wanted = frozenset(codes)

    def signal(case: Case) -> frozenset[str]:
        return frozenset(
            destination
            for destination in case.applicable(destinations)
            if case.reasons(destination) & wanted
        )

    return signal


def _shadow_disposition(disposition: str, codes: Iterable[str]) -> Signal:
    wanted = frozenset(codes)

    def signal(case: Case) -> frozenset[str]:
        return frozenset(
            destination
            for destination in case.applicable(_ALL)
            if any(
                held == disposition and code in wanted
                for held, code in case.shadow.get(destination, set())
            )
        )

    return signal


def _report(predicate: Callable[[Case], bool], destinations: frozenset[str]) -> Signal:
    def signal(case: Case) -> frozenset[str]:
        return case.applicable(destinations) if predicate(case) else frozenset()

    return signal


def _either(*signals: Signal) -> Signal:
    def signal(case: Case) -> frozenset[str]:
        return frozenset().union(*(each(case) for each in signals))

    return signal


def _cover(predicate: CoverPredicate) -> Signal:
    def signal(case: Case) -> frozenset[str]:
        if any(predicate(case, candidate) for candidate in case.cover_candidates):
            return case.applicable(frozenset({"cover"}))
        return frozenset()

    return signal


def _never(_case: Case) -> frozenset[str]:
    return frozenset()


# Automatic-decision abstention, split by engine (#1745). The scalar engine
# decides the source's _SUPPORTED_DESTINATIONS; every other destination's
# no_eligible_action is the rich-preview path's residual.
_A6_REASONS = frozenset({"insufficient_support", "no_eligible_action"})


def _abstaining(codes: frozenset[str], *, scalar: bool) -> Signal:
    def signal(case: Case) -> frozenset[str]:
        destinations = case.scalar if scalar else _ALL - case.scalar
        return frozenset(
            destination
            for destination in case.applicable(destinations)
            if any(
                held == "abstention" and code in codes
                for held, code in case.shadow.get(destination, set())
            )
        )

    return signal


def _isbn_claims(case: Case) -> list[dict[str, object]]:
    return [claim for claim in case.local_claims() if claim.get("kind") == "isbn"]


def _valid_families(case: Case) -> set[str]:
    families: set[str] = set()
    for claim in _isbn_claims(case):
        canonical = _text(claim.get("canonical"))
        if claim.get("state") == "valid" and canonical is not None:
            family = isbn_family(canonical)
            if family is not None:
                families.add(family)
    return families


def _usable_families(case: Case) -> set[str]:
    """The ISBN-first eligibility rule (#959) replayed on the stored claims."""
    families: set[str] = set()
    for claim in _isbn_claims(case):
        canonical = _text(claim.get("canonical"))
        if (
            canonical is not None
            and claim.get("applicability") in {"ebook", "unknown"}
            and claim.get("basis") == "opening_matter"
            and not claim.get("rear_provenance")
        ):
            family = isbn_family(canonical)
            if family is not None:
                families.add(family)
    return families


def _malformed_isbn(case: Case) -> bool:
    return any(
        claim.get("state") == "malformed" for claim in _isbn_claims(case)
    ) or any(
        evidence.get("relation") == "invalid" for evidence in case.identifier_evidence()
    )


def _unresolved_isbn(case: Case) -> bool:
    return any(
        evidence.get("relation") == "unresolved"
        for evidence in case.identifier_evidence()
    )


def _unscoped_isbn(case: Case) -> bool:
    return not any(
        evidence.get("relation") == "accepted"
        for evidence in case.identifier_evidence()
    ) and any(
        claim.get("state") == "valid"
        and claim.get("applicability") in {"print", "ambiguous", "unknown"}
        for claim in _isbn_claims(case)
    )


def _isbn_first_ineligible(case: Case) -> bool:
    return (
        case.epub and bool(_valid_families(case)) and len(_usable_families(case)) != 1
    )


def _diagnostic(stage: str, category: str) -> Callable[[Case], bool]:
    def predicate(case: Case) -> bool:
        return any(
            diagnostic.get("stage") == stage and diagnostic.get("category") == category
            for diagnostic in case.diagnostics()
        )

    return predicate


def _lane_status(status: str) -> Callable[[Case], bool]:
    def predicate(case: Case) -> bool:
        return any(lane.get("status") == status for lane in case.lanes()) or any(
            provider.get("status") == status for provider in case.providers()
        )

    return predicate


def _enrichment_overflow(case: Case) -> bool:
    return any(
        bool(provider.get("edition_enrichment_overflow"))
        for provider in case.providers()
    )


def _rear_budget(case: Case) -> bool:
    return "rear_resource_limit" in _list(
        _dict(case.report.get("local")).get("reasons")
    )


def _chapters_capped(case: Case) -> bool:
    audio = _dict(case.report.get("audio"))
    omitted = audio.get("chapter_rows_omitted")
    return audio.get("chapters_truncated") is True or (
        isinstance(omitted, int) and not isinstance(omitted, bool) and omitted > 0
    )


def _isbn_fan_out(case: Case) -> bool:
    return len(_valid_families(case)) > MAX_ISBN_DISCOVERY_LANES


def _bounded_text(case: Case) -> frozenset[str]:
    kinds = {
        kind
        for owner in (*_dicts(case.report.get("field_results")), *case.local_claims())
        if owner.get("state") in _BOUNDED_STATES
        and (kind := _text(owner.get("kind"))) is not None
    }
    destinations = frozenset().union(
        *(_CLAIM_DESTINATIONS.get(kind, frozenset()) for kind in kinds)
    )
    return case.applicable(destinations)


def _cover_source_refused(case: Case) -> frozenset[str]:
    refused = any(
        cover.get("state") in _COVER_SOURCE_REFUSALS
        for cover in _dicts(case.report.get("covers"))
    ) or any(
        reason.removeprefix("source_") in _COVER_SOURCE_REFUSALS
        for reason in case.cover_omissions
    )
    return case.applicable(frozenset({"cover"})) if refused else frozenset()


def _preview_capped(case: Case) -> frozenset[str]:
    capped = bool(case.cover_omissions & _PREVIEW_CAP_OMISSIONS)
    return case.applicable(frozenset({"cover"})) if capped else frozenset()


def _candidate_outcome(*outcomes: str) -> CoverPredicate:
    wanted = frozenset(outcomes)

    def predicate(_case: Case, candidate: dict[str, object]) -> bool:
        return candidate.get("outcome") in wanted

    return predicate


def _pictured(case: Case, candidate: dict[str, object]) -> frozenset[str]:
    return case.cover_records.get(
        _text(candidate.get("candidate_reference")) or "", frozenset()
    )


def _work_search_unknown(case: Case, candidate: dict[str, object]) -> bool:
    if candidate.get("outcome") != "unknown":
        return False
    records = _pictured(case, candidate)
    return bool(records) and all(
        case.lane_kinds.get(record, set()) == {"work"} for record in records
    )


# ---------------------------------------------------------------------------
# The gate catalogue. Every string here is a closed output string.


@dataclass(frozen=True, slots=True)
class Gate:
    gate_id: str
    family: str
    gate: str
    location: str
    origin: str
    corpus: str
    introduced_in: str
    destinations: str
    loosened: str
    control: Control
    signal: Signal = _never
    retained: bool = True
    # False where the persisted record cannot tell this gate's blocks from
    # blocks with other causes, so no count would be this gate's cost.
    separable: bool = True
    cover_predicate: CoverPredicate | None = None
    medium: str | None = None
    disposition: str = "kept"
    ranked: bool = True
    # Why a row that would score is kept out of the ranking.
    unranked_note: str | None = None
    # Measurable only where the source keeps rich refusals' own reasons.
    needs_rich_split: bool = False


_NOT_RECORDED = "not recorded"
_LANE = "every destination its provider lanes feed"
_ISBN = frozenset({"isbn"})
_COVER = frozenset({"cover"})

CATALOGUE: tuple[Gate, ...] = (
    Gate(
        "I1",
        "ISBN acceptance",
        "ISBN form and checksum",
        "domain/metadata_report.py _valid_isbn, isbn_family; epub_opening.py",
        "#291 (b48aae94); identifier_validity_invalid #816 (e3df6c21)",
        _NOT_RECORDED,
        "b48aae94fef6a603f106c49e13ca16f6cf536102",
        "isbn",
        "admit an ISBN whose form or checksum fails",
        "not_identity",
        _either(
            _report(_malformed_isbn, _ISBN),
            _reason({"identifier_validity_invalid"}, _ISBN),
        ),
    ),
    Gate(
        "I2",
        "ISBN acceptance",
        "ISBN occurrence scoping (label-only, owned by another identifier)",
        "adapters/evidence_extractors/epub_opening.py",
        "#385 (a70791af)",
        _NOT_RECORDED,
        "a70791afefcd7362a8f9fd4d5becaf9fe67fe08e",
        "isbn",
        "admit an ISBN occurrence another identifier owns",
        "not_retained",
        retained=False,
    ),
    Gate(
        "I3",
        "ISBN acceptance",
        "Local ISBN acceptance classes; anything else stays unresolved",
        "domain/metadata_report.py derive_identifier_evidence; domain/proposal_set.py",
        "#333 (f1dd28f5)",
        "accepted-evidence precision 38/40 (95%), recall 41/46",
        "f1dd28f5055df774c3127a0b56b313aae80a1387",
        "isbn",
        "admit the lowest-identifier alternative of an unresolved local ISBN",
        "alternative",
        _either(
            _reason({"identifier_applicability_unresolved"}, _ISBN),
            _report(_unresolved_isbn, _ISBN),
        ),
        disposition="already filed as #1356 (closed)",
    ),
    Gate(
        "I4",
        "ISBN acceptance",
        "Provider ISBN family applicability (print, ambiguous, unknown scope)",
        "application/metadata_report.py provider_isbn_applicability",
        "#388 (999edaf0)",
        _NOT_RECORDED,
        "999edaf0169ddf2734473423a3a5d5ce31a8a15a",
        "isbn",
        "admit the lowest-identifier alternative where only a print, ambiguous or unknown-scope "
        "local ISBN exists",
        "alternative",
        _report(_unscoped_isbn, _ISBN),
    ),
    Gate(
        "I5",
        "ISBN acceptance",
        "ISBN-first lane eligibility (opening-matter, ebook or unknown, no rear provenance, "
        "exactly one family)",
        "application/metadata_report.py usable_isbn_families, metadata_lookup",
        "#959 (9640228b)",
        _NOT_RECORDED,
        "9640228b8c369f0b6e2e0dc2839bb2aac883abee",
        _LANE,
        "query by any valid local ISBN family",
        "not_retained",
        _report(_isbn_first_ineligible, _ALL),
    ),
    Gate(
        "I6",
        "ISBN acceptance",
        "Competing ISBNs need a verified manifestation",
        "domain/proposal_set.py",
        "#979 (8056f4d3); ADR-0080",
        "development 42/42, sealed holdout 30/30",
        "8056f4d34500e1fab196be29dda290aba7a563cd",
        "isbn",
        "admit the lowest-identifier competing ISBN",
        "alternative",
        _reason({"manifestation_verification_required"}, _ISBN),
    ),
    Gate(
        "I7",
        "ISBN acceptance",
        "Audiobook ISBN refusal",
        "domain/rich_metadata_preview.py _audiobook_isbn_refusal",
        "#1059 (ed997a7f)",
        _NOT_RECORDED,
        "ed997a7f5606e9e58f790d3be291d790513fcb79",
        "isbn",
        "admit the lowest-identifier audiobook ISBN without a recording association",
        "alternative",
        _reason(
            {
                "source_value_malformed_or_incomplete",
                "source_values_conflict",
                "source_value_insufficient_association",
                "source_unavailable",
                "source_value_absent",
            },
            _ISBN,
        ),
        medium="audiobook",
    ),
    Gate(
        "W1",
        "Work and edition binding",
        "Exact work-identity admission (title and creators, transformation rules, "
        "decoration fail-closed)",
        "domain/work_identity.py; domain/metadata_report.py",
        "#870/#916 (a0958ac2); diagnostic #1268 (a4315d6a); ADR-0018, ADR-0067, ADR-0068",
        "89-case adjudicated corpus, zero false admissions",
        "a0958ac23e49b3c77377b1db030aa2c588e519fb",
        _LANE,
        "admit the refused work candidates",
        "not_retained",
        _report(_diagnostic("identity", "work_identity_refused"), _ALL),
        disposition="already filed as #1355",
    ),
    Gate(
        "W2",
        "Work and edition binding",
        "Work-identity contradiction (edition against work, ISBN against target work)",
        "domain/metadata_report.py",
        "#1014 (caba7b6e)",
        _NOT_RECORDED,
        "caba7b6ee4f3da93e6edb65f836bfdc45bed89a0",
        _LANE,
        "admit a contradicted work candidate",
        "not_retained",
        retained=False,
    ),
    Gate(
        "W3",
        "Work and edition binding",
        "Variant veto",
        "domain/metadata_report.py",
        "#916 (a0958ac2)",
        "as W1",
        "a0958ac23e49b3c77377b1db030aa2c588e519fb",
        _LANE,
        "admit a vetoed variant",
        "not_retained",
        retained=False,
    ),
    Gate(
        "W4",
        "Work and edition binding",
        "Adapter work and edition candidate admission",
        "adapters/providers/open_library_metadata.py; adapters/providers/hardcover_metadata.py",
        "#348 (65a884c9), #570 (e7862167)",
        _NOT_RECORDED,
        "65a884c9b5c178229b8da9906abc6414a02e91be",
        _LANE,
        "admit the rows an adapter refuses",
        "not_retained",
        retained=False,
    ),
    Gate(
        "W5",
        "Work and edition binding",
        "Target-work representation for work-scope fields",
        "domain/proposal_set.py provider_candidate_represents_target_work",
        "#972 (da35e6df)",
        "7 correct decisions per tag option, 100%, 49 works",
        "da35e6df4d56c975d496efffeb20df3a76e82289",
        "work-scope destinations",
        "admit the lowest-identifier alternative without target-work proof",
        "alternative",
        _reason({"target_work_applicability_unproved", "target_work_contradicted"}),
        disposition="already filed as #1355",
    ),
    Gate(
        "W6",
        "Work and edition binding",
        "Recording binding: runtime veto, tight band, target-recording representation",
        "domain/audiobook_corroboration.py; domain/proposal_set.py",
        "#1045 (0a2a193a), #1059 (ed997a7f); ADR-0064",
        "12 books, 4/4/4 development, confirmation, holdout",
        "0a2a193a6645e3f7529f626ddf9abd957ae01251",
        _LANE,
        "admit the products a recording ladder refused",
        "not_retained",
        # A lane's "refused" is W1's work_identity_refused diagnostic and
        # "empty_or_refused" covers empty searches too; the runtime veto and
        # band act at proposal time and leave no per-lane trace.
        separable=False,
        medium="audiobook",
    ),
    Gate(
        "W7",
        "Work and edition binding",
        "Corroborated band-agreeing rule and its permission",
        "domain/audiobook_corroboration.py; application/metadata_report.py",
        "#1463 (cca9067c), #1465 (120e60a4)",
        "14 of 42 probe items rescued; 0 different-work admissions in 57,570 cross-item pairs",
        "cca9067cb9f94b9ef4899eda59fd724d7df3d988",
        _LANE,
        "admit a band-agreeing product before every exact path refused",
        "not_retained",
        retained=False,
        medium="audiobook",
    ),
    Gate(
        "W8",
        "Work and edition binding",
        "Alternative WORK lane permission",
        "application/metadata_report.py alternative_work_lane_permitted",
        "#1269 (906e9a2a)",
        _NOT_RECORDED,
        "906e9a2a3bd5324e81b57cf27336fb7d6c20dbab",
        _LANE,
        "run the alternative WORK lane after any asserted outcome",
        "not_retained",
        retained=False,
        medium="audiobook",
    ),
    Gate(
        "W9",
        "Work and edition binding",
        "Series identity match for series fill",
        "domain/metadata_report.py; domain/rich_metadata_preview.py",
        "#998 (dc8de6f5); ADR-0070",
        _NOT_RECORDED,
        "dc8de6f51417c209aa95941864273a9fe8ebbac9",
        "series",
        "admit the lowest-identifier series alternative",
        "alternative",
        _reason(
            {"series_conflicting_membership", "series_membership_incomplete"},
            frozenset({"series"}),
        ),
    ),
    Gate(
        "C1",
        "Cover identity classifier",
        "Classifier outcomes (unknown, conflict block election)",
        "application/cover_identity.py classify_cover_identity",
        "#577 (f7e974d0), #588 (40a97a54); ADR-0046, ADR-0047, ADR-0059",
        "96/99 exact agreement; plausible 18/21, none falsely plausible",
        "40a97a54af7810b0efb052f6d2eb7bdb2dcdb143",
        "cover",
        "admit unknown and conflict cover candidates",
        "cover_candidates",
        _cover(_candidate_outcome("unknown", "conflict")),
        cover_predicate=_candidate_outcome("unknown", "conflict"),
        disposition="already filed as #1355",
    ),
    Gate(
        "C2",
        "Cover identity classifier",
        "Plausible only for ISBN-search records",
        "application/cover_identity.py",
        "#577 (f7e974d0), #588 (40a97a54)",
        "not measured at origin",
        "40a97a54af7810b0efb052f6d2eb7bdb2dcdb143",
        "cover",
        "admit unknown cover candidates reached only by work search",
        "cover_candidates",
        _cover(_work_search_unknown),
        cover_predicate=_work_search_unknown,
        disposition="already filed as #1380",
    ),
    Gate(
        "C3",
        "Cover identity classifier",
        "Audiobook cross-authority perceptual-hash corroboration",
        "application/cover_identity.py _cover_corroboration_reasons",
        "#948 (6423552f); ADR-0064",
        _NOT_RECORDED,
        "6423552fa6315ebae8fb632c437f56b8eaaa43ad",
        "cover",
        "admit an audiobook cover candidate one authority pictures",
        # The rule only adds reasons to a classified candidate; the outcome
        # comes from _classify_sources, which never reads them (ADR-0064).
        "informational",
        medium="audiobook",
    ),
    Gate(
        "C4",
        "Cover identity classifier",
        "Cover source admission (media type, byte bound, declaration validity)",
        "adapters/providers/remote_cover.py; adapters/evidence_extractors/epub_cover.py",
        "#298 (cbd2a835)",
        _NOT_RECORDED,
        "cbd2a835b59321ff0536367f7ef8ec9361673648",
        "cover",
        "admit a refused, truncated, oversized or undeclared cover source",
        "not_identity",
        _cover_source_refused,
        disposition="already filed as #1357 (closed)",
    ),
    Gate(
        "C5",
        "Cover identity classifier",
        "Audiobook cover withheld",
        "domain/proposal_set.py",
        "#985 (0ce358e9)",
        _NOT_RECORDED,
        "0ce358e9d4796fbab26aab771850d2e76ade05d6",
        "cover",
        "admit the lowest-identifier withheld audiobook cover",
        "alternative",
        _reason({"audiobook_cover_withheld"}, _COVER),
        medium="audiobook",
    ),
    Gate(
        "X1",
        "Candidate exclusions",
        "Candidate limit (an over-limit lane keeps no candidate)",
        "domain/metadata_report.py MAX_CANDIDATES",
        "#297 (f564e598)",
        _NOT_RECORDED,
        "f564e5987ed68f7f978f62e4d852ba5f8181d941",
        _LANE,
        "keep the first five candidates of an over-limit lane",
        "not_retained",
        _report(_lane_status("candidate_limit_exceeded"), _ALL),
    ),
    Gate(
        "X2",
        "Candidate exclusions",
        "Edition-enrichment overflow keeps a work-only witness",
        "domain/metadata_report.py",
        "#621 (254ed08f); ADR-0056",
        _NOT_RECORDED,
        "254ed08fe83d3f8375b1f2729919093ecddfa241",
        _LANE,
        "keep the overflowing editions",
        "not_retained",
        _report(_enrichment_overflow, _ALL),
    ),
    Gate(
        "X3",
        "Candidate exclusions",
        "Wrong-language, control-number and untidy-author value exclusion",
        "domain/provider_value_language.py; domain/proposal_set.py",
        "#1497/#1560 (ce3a172d)",
        _NOT_RECORDED,
        "ce3a172d7aff06feacd604e98770422fb39a67a8",
        "language-bearing and creator destinations",
        "admit the excluded values",
        "not_retained",
        retained=False,
    ),
    Gate(
        "X4",
        "Candidate exclusions",
        "Generic provider subtitle",
        "domain/proposal_set.py",
        "#817 (660fac2f)",
        "not recorded (corpus private)",
        "660fac2f11f01028f3a9b8ca407efc799209951a",
        "subtitle",
        "admit the lowest-identifier generic subtitle",
        "alternative",
        _reason({"generic_provider_subtitle"}),
    ),
    Gate(
        "X5",
        "Candidate exclusions",
        "Redundant publication context",
        "domain/proposal_set.py",
        "#818 (e28584e6)",
        _NOT_RECORDED,
        "e28584e6fa36fd280e7321452646fa824e7a761b",
        "publication destinations",
        "admit the lowest-identifier redundant publication value",
        "alternative",
        _reason({"redundant_publication_context"}),
    ),
    Gate(
        "T1",
        "Title and creator comparison",
        "More than one value group is an unresolved choice (equality by closed title "
        "and complete-creator agreement)",
        "domain/proposal_set.py _evaluate; domain/field_value_comparison.py "
        "canonical_text, values_match",
        "#351 (d35215e8); ADR-0020; equality forms #414 (3586e1be), #815 (ce76c7fd), "
        "ADR-0018",
        _NOT_RECORDED,
        "d35215e8cc3ceef1d194d38fd2a48a0084f29fb2",
        "every compared destination except cover",
        "admit the lowest-identifier of the competing values",
        "alternative",
        # A cover's competing_evidence is _cover_result's multi-candidate
        # branch, which cover election (C1) governs, not value comparison.
        _reason({"competing_evidence"}, _ALL - _COVER),
        disposition="kept (#1744)",
    ),
    Gate(
        "T2",
        "Title and creator comparison",
        "Evidence-led title identity key and ISBN-title material conflict",
        "domain/field_value_comparison.py; domain/proposal_set.py",
        "#1040 (c1963d97); ADR-0075",
        "constructor controls 50/50 and 75/75",
        "c1963d9714cc04d9e22db0c137e4685d2e55ecf6",
        "title",
        "admit the lowest-identifier title despite a material ISBN-title conflict",
        "alternative",
        _reason({"isbn_title_material_conflict"}),
    ),
    Gate(
        "T3",
        "Title and creator comparison",
        "Decoration-equivalence election and its dissent reasons",
        "domain/proposal_set.py",
        "#981 (c843ea7f); ADR-0067",
        "sealed 89-case admission corpus, 0 false admissions; 1,988-report archived replay",
        "c843ea7fb8be5a97dd6414410d4a30089ee7fc57",
        "title, subtitle",
        "admit the lowest-identifier title despite decoration dissent",
        "alternative",
        _reason(
            {
                "decoration_tidy_series_unpreserved",
                "decoration_equivalence_provider_dissent",
                "decoration_equivalence_decorated_consensus",
            }
        ),
    ),
    Gate(
        "T4",
        "Title and creator comparison",
        "Latin combining-mark work identity",
        "domain/work_identity.py",
        "#982 (f9be2ca2); ADR-0068",
        _NOT_RECORDED,
        "f9be2ca2be113ef5e6172b63985baf397783a517",
        _LANE,
        "admit a combining-mark variant work",
        "not_retained",
        retained=False,
    ),
    Gate(
        "A1",
        "Applicability and abstention",
        "Replace only with positive preservation evidence",
        "domain/proposal_set.py",
        "#351 (d35215e8)",
        "54 exact replays, zero measured systemic false positives",
        "d35215e8cc3ceef1d194d38fd2a48a0084f29fb2",
        "every proposable destination",
        "admit the lowest-identifier alternative without preservation evidence",
        "alternative",
        _reason({"no_positive_preservation_evidence"}),
    ),
    Gate(
        "A2",
        "Applicability and abstention",
        "Independent corroboration required",
        "domain/proposal_set.py",
        "#351 (d35215e8); narrators #966 (0ce358e9), #1642 (5558b7b3)",
        "as A1",
        "d35215e8cc3ceef1d194d38fd2a48a0084f29fb2",
        "a populated field other than genres, tags and description; "
        "narrators also when empty",
        "admit the only alternative, which one source states",
        "alternative",
        _reason({"insufficient_independent_corroboration"}),
        disposition="kept (#1747)",
    ),
    Gate(
        "A3",
        "Applicability and abstention",
        "Coherent bibliographic combination",
        "domain/proposal_set.py",
        "#429 (cef7644a); ADR-0021; same-name series #1099 (79dbbf4c); "
        "withheld titles #1497 (ce3a172d)",
        _NOT_RECORDED,
        "cef7644a89bc98ac1d9dca037dfb6d535fb28dd8",
        "title, subtitle and series",
        "none: the lowest-identifier rule is rejected; closed relaxations in the "
        "A3 analysis",
        "alternative",
        _reason({"no_safe_bibliographic_combination"}),
        disposition="kept (ADR-0097)",
    ),
    Gate(
        "A4",
        "Applicability and abstention",
        "Date refinement and year conflict need corroboration",
        "domain/proposal_set.py",
        "#351 (d35215e8), #415 (0d0021d1); ADR-0019",
        _NOT_RECORDED,
        "d35215e8cc3ceef1d194d38fd2a48a0084f29fb2",
        "publishedDate, publishedYear",
        "admit the lowest-identifier uncorroborated date",
        "alternative",
        _reason({"date_refinement_requires_corroboration", "date_year_conflict"}),
    ),
    Gate(
        "A5",
        "Applicability and abstention",
        "Cover gallery incomplete",
        "domain/proposal_set.py",
        "#351 (d35215e8)",
        _NOT_RECORDED,
        "d35215e8cc3ceef1d194d38fd2a48a0084f29fb2",
        "cover",
        "admit the lowest-identifier cover of an incomplete gallery",
        "alternative",
        _reason({"cover_gallery_incomplete"}, _COVER),
    ),
    Gate(
        "A6a",
        "Applicability and abstention",
        "Automatic-decision abstention, scalar engine (insufficient support, no "
        "eligible action)",
        "domain/shadow_decision.py _candidate_is_eligible, _base_reason",
        "#442 (593e6198); ADR-0036; re-scoped #1745, ADR-0092",
        "development 159/172 correct at 100% precision; sealed 12/12 and 43/43",
        "593e619821175655dd15c24e4717104eca0d722f",
        "the source's scalar decision destinations",
        "none: the lowest-identifier rule is rejected; closed relaxations in the "
        "A6 analysis",
        "alternative",
        # Only the decision's own reasons: evidence_sufficiency_manual repeats
        # the proposal set's refusal and is reported in the A6 analysis.
        _abstaining(_A6_REASONS, scalar=True),
        disposition="kept (ADR-0092)",
        ranked=False,
        unranked_note="kept by ADR-0092; its own cost is the proposable population "
        "in the A6 analysis",
    ),
    Gate(
        "A6b",
        "Applicability and abstention",
        "Rich-preview no-eligible-action leftover",
        "domain/shadow_outcome.py _rich_field_outcome",
        "#442 (593e6198); #1745, ADR-0092",
        _NOT_RECORDED,
        "593e619821175655dd15c24e4717104eca0d722f",
        "rich-preview destinations",
        "none: refile under the rich-preview reason's own row",
        "not_identity",
        _abstaining(frozenset({"no_eligible_action"}), scalar=False),
        disposition="kept (ADR-0092)",
        ranked=False,
        unranked_note="leftover of rich-preview refusals outside their own reasons",
        needs_rich_split=True,
    ),
    Gate(
        "A7",
        "Applicability and abstention",
        "Owner-reserved and write-safety holds",
        "domain/proposal_set.py",
        "#351 (d35215e8)",
        "not applicable",
        "d35215e8cc3ceef1d194d38fd2a48a0084f29fb2",
        "subjective and unusable-target destinations",
        "none: listed for completeness",
        "alternative",
        _reason({"populated_subjective_field", "target_state_unusable"}),
        disposition="kept (out of scope)",
        ranked=False,
    ),
    Gate(
        "B1",
        "Evidence-dropping bounds",
        "Local text bounds and unsafe-text sentinels",
        "adapters/evidence_extractors/epub_local.py _bounded; domain/evidence_bounds.py",
        "#296 (ef49d6c2), 102548c0",
        _NOT_RECORDED,
        "ef49d6c2956a6975595bef971807d85aefd76f0b",
        "the destinations the bounded local claim feeds",
        "admit oversized or unsafe local text",
        "not_identity",
        _bounded_text,
    ),
    Gate(
        "B2",
        "Evidence-dropping bounds",
        "Discovery fan-out caps (8 ISBN lanes, 9 queries, 4 ASIN lanes)",
        "application/metadata_report.py",
        "#388 (999edaf0), #916, #948",
        _NOT_RECORDED,
        "999edaf0169ddf2734473423a3a5d5ce31a8a15a",
        _LANE,
        "query every distinct local ISBN family",
        "not_retained",
        _report(_isbn_fan_out, _ALL),
    ),
    Gate(
        "B3",
        "Evidence-dropping bounds",
        "EPUB rear-resource budget",
        "domain/epub_opening.py",
        "#329 (21506f7f)",
        _NOT_RECORDED,
        "21506f7f2cdb220bf2e5c881a085e7002b8e0bea",
        "isbn",
        "read every rear resource",
        "not_retained",
        _report(_rear_budget, _ISBN),
    ),
    Gate(
        "B4",
        "Evidence-dropping bounds",
        "Audio chapter cap",
        "domain/audio_inspection.py",
        "#931/#934 (c3214949)",
        _NOT_RECORDED,
        "c3214949f40c1081116a64a1f3e71063c56531d7",
        _LANE,
        "keep every chapter row",
        "not_retained",
        _report(_chapters_capped, _ALL),
        medium="audiobook",
    ),
    Gate(
        "B5",
        "Evidence-dropping bounds",
        "Cover packet preview cap",
        "application/cover_packet_semantics.py MAX_COVER_PREVIEWS",
        "#588 (40a97a54)",
        _NOT_RECORDED,
        "40a97a54af7810b0efb052f6d2eb7bdb2dcdb143",
        "cover",
        "keep every preview",
        "not_retained",
        _preview_capped,
    ),
    Gate(
        "B6",
        "Evidence-dropping bounds",
        "Provider deadline share (20 s)",
        "application/metadata_report.py _PROVIDER_DEADLINE_SHARE_SECONDS",
        "#1273 (bcdee458)",
        _NOT_RECORDED,
        "bcdee458e533ee131b0422aad82548da2aeb4bbd",
        _LANE,
        "let a provider run past its share",
        "not_retained",
        # The report keeps a non-retryable request timeout and an undispatched
        # count, but not whether the share, the aggregate deadline, a
        # transport timeout or a sibling's terminal failure caused them.
        separable=False,
    ),
)

_CONTROL_TEXT: dict[str, str] = {
    "not_identity": "not estimable from natural controls",
    "not_retained": "not estimable: the refused records were not retained",
    "informational": "not estimable: the rule adds reasons and refuses nothing",
}
_STATUS_TEXT: dict[str, str] = {
    "gate_postdates_source": "not measurable on that snapshot: gate postdates source",
    "block_not_retained": "not measurable on that snapshot: block not retained",
    "cause_not_separable": "not measurable on that snapshot: cause not separable",
    "rich_refusals_collapsed": "not separable on that snapshot: rich-preview "
    "refusals collapsed",
}


# ---------------------------------------------------------------------------
# Counting and controls.


@dataclass(slots=True)
class Split:
    """Field counts for a row, or for one destination or medium of it (#1744)."""

    fields: int = 0
    true_positive: int = 0
    false_positive: int = 0
    unlabelled: int = 0
    # blocked fields whose loosened rule reaches no provider record at all
    unreached: int = 0
    # blocked fields the loosened rule has something to admit for
    actionable: int = 0
    # Decision-level rows only: every actionable field falls in exactly one of
    # the next five classes. Fields with two or more alternatives where exactly
    # one alternative's evidence is bound to this work and clean (the controls
    # decide) ...
    decidable: int = 0
    # ... and of those, the fields where the lowest-identifier pick is it
    pick_agrees: int = 0
    # fields where two or more alternatives are bound to this work
    tied: int = 0
    # fields where no alternative is clean, but one's evidence reaches this
    # work's records together with another work's
    contaminated: int = 0
    # fields where no alternative's evidence reaches this work's records
    no_right_work: int = 0
    # fields that kept one alternative: nothing competes with it
    single_alternative: int = 0
    # actionable fields whose pick preserves the current value
    pick_preserves: int = 0
    # Decision-level rows only: blocked fields one of the snapshot's automatic
    # decisions already settled (#1747), by taking the pick, leaving the
    # current value (keep_current or ignore), or anything else
    automatic_pick: int = 0
    automatic_keep: int = 0
    automatic_other: int = 0

    def add(self, other: Split) -> None:
        for item in fields(Split):
            setattr(
                self, item.name, getattr(self, item.name) + getattr(other, item.name)
            )


@dataclass(slots=True)
class Tally(Split):
    works: int = 0
    by_destination: Counter[str] = field(default_factory=Counter)
    by_medium: Counter[str] = field(default_factory=Counter)
    # blocked fields competing on a destination a later election decides
    election_fields: int = 0
    # decision-level rows only: the same counts per destination and per medium
    destinations: dict[str, Split] = field(default_factory=dict)
    media: dict[str, Split] = field(default_factory=dict)
    # whether the snapshot stored automatic decisions at all
    automatic_recorded: bool = False


# Elections that decide competing values on these destinations on main. A
# snapshot whose source predates one left those fields to the bare evaluator,
# so its ceiling there is stale for main.
LATER_ELECTIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("#974", "1a3878197cde7b06c215b15e51d1fd316a1e9fc7", frozenset({"title"})),
    (
        "#978",
        "68bda99a5813e5f1de1fe3d7ef3d64e9dc29a4d6",
        frozenset({"authors", "narrators"}),
    ),
    ("#979", "8056f4d34500e1fab196be29dda290aba7a563cd", frozenset({"isbn"})),
    ("#981", "c843ea7fb8be5a97dd6414410d4a30089ee7fc57", frozenset({"title"})),
    ("#1040", "c1963d9714cc04d9e22db0c137e4685d2e55ecf6", frozenset({"title"})),
)
_COMPETING = "competing_evidence"


def later_elections(revision: str, ancestry: Ancestry) -> tuple[str, ...]:
    """The elections on main that the snapshot's source revision predates."""
    return tuple(
        label
        for label, commit, _destinations in LATER_ELECTIONS
        if not ancestry(commit, revision)
    )


def _election_destinations(labels: Iterable[str]) -> frozenset[str]:
    wanted = set(labels)
    return frozenset().union(
        *(
            destinations
            for label, _commit, destinations in LATER_ELECTIONS
            if label in wanted
        )
    )


def _actionable(gate: Gate, case: Case, destination: str) -> bool:
    """Whether loosening the gate could unblock the field at all.

    A decision-level rule admits one alternative, so a field whose result
    kept no alternative stays blocked however the rule is loosened. A
    cover field is blocked here only because a matching candidate exists.
    """
    if gate.control == "alternative":
        return bool(_dicts(case.results.get(destination, {}).get("alternatives")))
    return gate.control == "cover_candidates"


def _pick(result: Mapping[str, object]) -> dict[str, object]:
    """The alternative a loosened decision-level rule would admit.

    The product ranks nothing: a result's alternatives are ordered by
    ``alternative_id``, a SHA-256 digest. The pick is the selected alternative
    where the result names one, otherwise the lowest identifier, which is an
    arbitrary choice among the competitors.
    """
    alternatives = _dicts(result.get("alternatives"))
    selected = _text(result.get("selected_alternative_id"))
    for item in alternatives:
        if selected is not None and item.get("alternative_id") == selected:
            return item
    return min(
        alternatives,
        key=lambda item: _text(item.get("alternative_id")) or "",
        default={},
    )


def _evidence_labels(case: Case, alternative: Mapping[str, object]) -> set[str]:
    return {
        case.labels.get(record, "unlabelled")
        for record in case.resolve(_list(alternative.get("evidence_references")))
    }


def _right_work(case: Case, alternative: Mapping[str, object]) -> bool:
    """Whether the alternative's evidence is bound to this work and only to it."""
    labels = _evidence_labels(case, alternative)
    return "positive" in labels and "negative" not in labels


def _classify_pick(case: Case, result: Mapping[str, object], counts: Split) -> None:
    """Count one actionable field into exactly one class, and the pick's effect."""
    alternatives = _dicts(result.get("alternatives"))
    if not alternatives:
        return
    if _pick(result).get("assessment") == "preserves_target":
        counts.pick_preserves += 1
    if len(alternatives) < 2:
        counts.single_alternative += 1
        return
    right = [item for item in alternatives if _right_work(case, item)]
    if len(right) == 1:
        counts.decidable += 1
        if _pick(result) is right[0]:
            counts.pick_agrees += 1
    elif len(right) > 1:
        counts.tied += 1
    elif any("positive" in _evidence_labels(case, item) for item in alternatives):
        counts.contaminated += 1
    else:
        counts.no_right_work += 1


def _count_automatic(
    case: Case, destination: str, result: Mapping[str, object], counts: Split
) -> None:
    """Count a blocked field the snapshot's automatic decision already settled."""
    decision = case.automatic.get(destination)
    if decision is None:
        return
    outcome, candidate = decision
    picked = _text(_pick(result).get("alternative_id"))
    # Both leave the field as it is; the product's activity record says so.
    if outcome in {"keep_current", "ignore"}:
        counts.automatic_keep += 1
    elif outcome == "candidate" and picked is not None and candidate == picked:
        counts.automatic_pick += 1
    else:
        counts.automatic_other += 1


def _reached(gate: Gate, case: Case, destination: str) -> frozenset[str]:
    if gate.control == "alternative":
        pick = _pick(case.results.get(destination, {}))
        return case.resolve(_list(pick.get("evidence_references")))
    if gate.control == "cover_candidates" and gate.cover_predicate is not None:
        predicate = gate.cover_predicate
        return frozenset().union(
            *(
                _pictured(case, candidate)
                for candidate in case.cover_candidates
                if predicate(case, candidate)
            )
        )
    return frozenset()


def _tally_case(
    gate: Gate, case: Case, tally: Tally, elections: frozenset[str] = frozenset()
) -> None:
    if gate.medium is not None and case.medium != gate.medium:
        return
    blocked = sorted(
        destination for destination in gate.signal(case) if case.blocked(destination)
    )
    if not blocked:
        return
    tally.works += 1
    for destination in blocked:
        one = Split(fields=1, actionable=int(_actionable(gate, case, destination)))
        tally.by_destination[destination] += 1
        tally.by_medium[case.medium] += 1
        result = case.results.get(destination, {})
        if destination in elections and _COMPETING in _list(result.get("reasons")):
            tally.election_fields += 1
        reached = _reached(gate, case, destination)
        if not reached and gate.control in ("alternative", "cover_candidates"):
            one.unreached = 1
        for record in reached:
            label = case.labels.get(record, "unlabelled")
            if label == "positive":
                one.true_positive += 1
            elif label == "negative":
                one.false_positive += 1
            else:
                one.unlabelled += 1
        if gate.control == "alternative":
            _classify_pick(case, result, one)
            _count_automatic(case, destination, result, one)
            tally.destinations.setdefault(destination, Split()).add(one)
            tally.media.setdefault(case.medium, Split()).add(one)
        tally.add(one)


def wilson_lower_bound(successes: int, total: int, z: float = WILSON_Z) -> float | None:
    """The lower limit of the two-sided Wilson score interval, or None at n = 0."""
    if total <= 0:
        return None
    phat = successes / total
    denominator = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


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


def _open_snapshot(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise Refusal("database unreadable")
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise Refusal(
            "database has a non-empty write-ahead log; checkpoint a copy first"
        )
    absolute = quote(str(path.absolute()), safe="/")
    try:
        connection = sqlite3.connect(f"file:{absolute}?mode=ro&immutable=1", uri=True)
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    except sqlite3.Error:
        raise Refusal("database unreadable") from None
    if not _REQUIRED_TABLES <= tables:
        connection.close()
        raise Refusal("snapshot schema unrecognised")
    return connection


def _automatic_decisions(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, tuple[str | None, str | None]]] | None:
    """Each case's stored automatic decisions, or None where none are recorded.

    The table is optional: a source that predates it, or a copy without it,
    reports "not recorded" rather than refusing.
    """
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "automatic_decisions" not in tables:
        return None
    query = "SELECT review_case_id,destination,document FROM automatic_decisions"
    if "metadata_policy_state" in tables:
        # A decision settles its field only in the active policy generation;
        # earlier generations' rows are history (ADR-0036, #1573), as in the
        # product's own review queries. The generation is read from the
        # document, which every source stores it in.
        query += (
            " WHERE json_extract(document,'$.policy_generation')="
            "(SELECT generation FROM metadata_policy_state)"
        )
    # Without the policy state no active generation can be read, so every
    # stored decision counts, as before.
    decisions: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    rows = connection.execute(query)
    for case_id, destination, document in rows:
        stored = _document(document)
        decisions.setdefault(case_id, {})[destination] = (
            _text(stored.get("outcome")),
            _text(stored.get("candidate_id")),
        )
    return decisions


def measure_snapshot(
    connection: sqlite3.Connection,
    statuses: Mapping[str, Status],
    elections: Iterable[str] = (),
    scalar: frozenset[str] = frozenset(shadow_decision._SUPPORTED_DESTINATIONS),
) -> tuple[dict[str, Tally], Counter[str]]:
    """Stream current cases once, tallying every measurable gate."""
    later = _election_destinations(elections)
    measurable = [gate for gate in CATALOGUE if statuses[gate.gate_id] == "measurable"]
    media: Counter[str] = Counter()
    try:
        automatic = _automatic_decisions(connection)
        tallies = {
            gate.gate_id: Tally(automatic_recorded=automatic is not None)
            for gate in measurable
        }
        rows = connection.execute(_CASES_SQL)
        for case_id, case_text, report_text, shadow_text, consideration_text in rows:
            case = _build_case(
                _document(case_text),
                _document(report_text),
                _document(shadow_text),
                _document(consideration_text),
            )
            case.automatic = (automatic or {}).get(case_id, {})
            case.scalar = scalar
            media[case.medium] += 1
            for gate in measurable:
                _tally_case(gate, case, tallies[gate.gate_id], later)
    except sqlite3.Error:
        raise Refusal("snapshot schema unrecognised") from None
    return tallies, media


# ---------------------------------------------------------------------------
# Ancestry.


def git_ancestry(repository: Path) -> Ancestry:
    """Decide ancestry with git, refusing on a shallow or unknown history."""

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    shallow = run("rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        raise Refusal("repository history is shallow or unreadable")
    known: set[str] = set()

    def ancestry(commit: str, revision: str) -> bool:
        for value in (commit, revision):
            if value not in known:
                if run("cat-file", "-e", f"{value}^{{commit}}").returncode != 0:
                    raise Refusal("unknown revision")
                known.add(value)
        result = run("merge-base", "--is-ancestor", commit, revision)
        if result.returncode not in (0, 1):
            raise Refusal("ancestry undecidable")
        return result.returncode == 0

    return ancestry


def gate_statuses(
    revision: str, ancestry: Ancestry, rich_split: bool = True
) -> dict[str, Status]:
    statuses: dict[str, Status] = {}
    for gate in CATALOGUE:
        if not ancestry(gate.introduced_in, revision):
            statuses[gate.gate_id] = "gate_postdates_source"
        elif gate.needs_rich_split and not rich_split:
            statuses[gate.gate_id] = "rich_refusals_collapsed"
        elif not gate.retained:
            statuses[gate.gate_id] = "block_not_retained"
        elif not gate.separable:
            statuses[gate.gate_id] = "cause_not_separable"
        else:
            statuses[gate.gate_id] = "measurable"
    return statuses


# ---------------------------------------------------------------------------
# Attention per case, read by the snapshot's own source in a child process.

_ATTENTION_CHILD = r"""
import json, os, sqlite3, sys
from urllib.parse import quote
root = os.path.realpath(sys.argv[1])
sys.path[:0] = [os.path.join(root, "src"), root]
import mechanical_librarian
from mechanical_librarian.adapters.persistence import review_sql
import scripts.cold_start_report as report_module
from mechanical_librarian.domain import shadow_decision, shadow_outcome
for module in (mechanical_librarian, review_sql, report_module, shadow_decision):
    if not os.path.realpath(module.__file__).startswith(root + os.sep):
        raise SystemExit(3)
R = review_sql
if hasattr(R, "_STORED_COVER_ATTENTION_AUTHORITY_SQL"):
    predicate = (R._REVIEW_FIELD_ATTENTION_REASON_SQL + " AND "
                 + R._STORED_COVER_ATTENTION_AUTHORITY_SQL
                 + R._REVIEW_FIELD_ATTENTION_CLEAR_SQL)
else:
    predicate = R._REVIEW_FIELD_ATTENTION_SQL
shadow = ("(SELECT json_extract(outcome.value,'$.reason_code') "
          "FROM shadow_decision_outcomes shadow,"
          "json_each(shadow.document,'$.outcomes') outcome "
          "WHERE shadow.review_case_id=rc.review_case_id "
          "AND json_extract(outcome.value,'$.destination')=result.destination "
          "AND json_extract(outcome.value,'$.disposition')='abstention' LIMIT 1)")
per_case = ("SELECT rc.review_case_id,result.destination,"
            "json_extract(canonical_result.value,'$.reasons')," + shadow + ","
            "CASE WHEN json_extract(mr.document,'$.audio') IS NULL "
            "THEN 'ebook' ELSE 'audiobook' END "
            + R._CURRENT_REVIEW_FROM_SQL
            + " JOIN review_case_field_facts result "
            "ON result.review_case_id=rc.review_case_id,"
            "json_each(rc.document,'$.results') canonical_result WHERE "
            "json_extract(canonical_result.value,'$.destination')=result.destination AND "
            + predicate + R._EPUB_INAPPLICABLE_ATTENTION_SQL)
constant = getattr(report_module, "_STORED_ATTENTION_FIELD_ROWS_SQL", None)
if constant is None:
    constant = report_module._ATTENTION_FIELD_ROWS_SQL
excluded = constant
if R._EPUB_INAPPLICABLE_ATTENTION_SQL not in constant:
    excluded = constant + R._EPUB_INAPPLICABLE_ATTENTION_SQL
path = quote(os.path.abspath(sys.argv[2]), safe="/")
connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
connection.execute("PRAGMA query_only=ON")
connection.create_function("ml_cover_consideration_current", 2, lambda _d, _r: 0)
ordinals = {}
rows = []
for case, destination, reasons, shadow_code, medium in connection.execute(per_case):
    ordinal = ordinals.setdefault(case, len(ordinals))
    if isinstance(shadow_code, str) and shadow_code:
        causes = [shadow_code]
    else:
        loaded = json.loads(reasons) if isinstance(reasons, str) and reasons else []
        causes = sorted({item for item in loaded if isinstance(item, str)})
    rows.append([ordinal, destination, causes, medium])
report_rows = len(connection.execute(constant).fetchall())
excluded_rows = len(connection.execute(excluded).fetchall())
sys.stdout.write(json.dumps({"rows": rows, "report_rows": report_rows,
                             "report_rows_excluded": excluded_rows,
                             "scalar": sorted(shadow_decision._SUPPORTED_DESTINATIONS),
                             "rich_split": hasattr(shadow_outcome,
                                                   "_RICH_REFUSAL_REASONS")}))
"""


@dataclass(frozen=True, slots=True)
class AttentionProbe:
    rows: tuple[tuple[int, str, tuple[str, ...], str], ...]
    report_rows: int
    report_rows_excluded: int
    # The source's scalar decision destinations and whether its outcomes keep
    # rich refusals' own reasons (#1745). A probe that does not state them
    # (a test double) reads the checkout's.
    scalar: tuple[str, ...] | None = None
    rich_split: bool | None = None


AttentionReader = Callable[[Path, Path], AttentionProbe]


def child_attention(source_root: Path, database: Path) -> AttentionProbe:
    """Run the source revision's own attention query in an isolated child."""
    if not (source_root / "src" / "mechanical_librarian").is_dir():
        raise Refusal("source root is not an exported source tree")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _ATTENTION_CHILD,
                str(source_root),
                str(database),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Refusal("attention probe failed") from None
    if completed.returncode != 0:
        raise Refusal("attention probe failed")
    try:
        loaded = _dict(json.loads(completed.stdout))
        rows = tuple(
            (
                cast("int", row[0]),
                cast("str", row[1]),
                tuple(cast("list[str]", row[2])),
                cast("str", row[3]),
            )
            for row in cast("list[list[object]]", loaded["rows"])
        )
        return AttentionProbe(
            rows,
            cast("int", loaded["report_rows"]),
            cast("int", loaded["report_rows_excluded"]),
            tuple(cast("list[str]", loaded["scalar"])),
            cast("bool", loaded["rich_split"]),
        )
    except (KeyError, IndexError, TypeError, ValueError):
        raise Refusal("attention probe failed") from None


def _reason_vocabulary() -> frozenset[str]:
    reasons = {
        reason
        for sets in _OUTCOME_REASON_SETS.values()
        for tuple_ in sets
        for reason in tuple_
    }
    reasons |= set(shadow_outcome._REASON_CODES)
    reasons |= set(shadow_decision._REASON_CODES)
    reasons |= {_SAFETY_CAUSE}
    return frozenset(reasons)


REASONS = _reason_vocabulary()


def summarise_attention(probe: AttentionProbe) -> dict[str, object]:
    """Fields and cases per medium, with and without covers, by destination and reason."""
    cases: dict[str, dict[str, Counter[int]]] = {
        medium: {"with_covers": Counter(), "without_covers": Counter()}
        for medium in _MEDIA
    }
    by_destination: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    for ordinal, destination, causes, medium in probe.rows:
        if destination not in _ALL or medium not in _MEDIA:
            raise Refusal("attention probe returned an unknown vocabulary")
        cases[medium]["with_covers"][ordinal] += 1
        if destination != "cover":
            cases[medium]["without_covers"][ordinal] += 1
        by_destination[destination] += 1
        for cause in causes or (_SAFETY_CAUSE,):
            by_reason[cause if cause in REASONS else _OTHER_REASON] += 1
    every_case = {ordinal for ordinal, *_ in probe.rows}
    return {
        "fields": len(probe.rows),
        "cases": len(every_case),
        "report_query_fields": probe.report_rows,
        "epub_inapplicable_fields": probe.report_rows - probe.report_rows_excluded,
        "media": {
            medium: {
                basis: {"fields": sum(counter.values()), "cases": len(counter)}
                for basis, counter in bases.items()
            }
            for medium, bases in cases.items()
        },
        "by_destination": dict(sorted(by_destination.items())),
        "by_reason": dict(sorted(by_reason.items())),
    }


def reconcile_report(
    attention: Mapping[str, object], report: Mapping[str, object]
) -> bool:
    """Compare attention totals with the cold-start report its source produced."""
    libraries = _dicts(report.get("libraries"))
    stored = [_dict(library.get("attention_stored_basis")) for library in libraries]
    if libraries and all(stored):
        fields = sum(
            cast("int", basis.get("cover_fields_with_owner_actions", 0))
            + cast("int", _dict(basis.get("non_cover_fields")).get("total", 0))
            for basis in stored
        )
        works = sum(cast("int", basis.get("works", 0)) for basis in stored)
    else:
        fields = sum(
            cast("int", _dict(library.get("attention_fields")).get("total", 0))
            for library in libraries
        )
        works = cast("int", _dict(report.get("totals")).get("attention_works", 0))
    return fields == attention["report_query_fields"] and works == attention["cases"]


# ---------------------------------------------------------------------------
# Assembly, ranking and closed output.


@dataclass(frozen=True, slots=True)
class SnapshotInput:
    label: str
    database: Path
    revision: str
    source_root: Path
    source_report: Path | None


def _row(
    gate: Gate,
    snapshots: Sequence[tuple[str, Mapping[str, Status], Mapping[str, Tally]]],
    follow_ons: Mapping[str, int],
) -> dict[str, object]:
    per_snapshot: list[dict[str, object]] = []
    pooled = Split()
    destinations: dict[str, Split] = {}
    media: dict[str, Split] = {}
    measurable_anywhere = False
    automatic_recorded = True
    estimable_control = gate.control in ("alternative", "cover_candidates")
    picks = gate.control == "alternative"
    for label, statuses, tallies in snapshots:
        status = statuses[gate.gate_id]
        entry: dict[str, object] = {"label": label, "status": status}
        if status == "measurable":
            measurable_anywhere = True
            tally = tallies[gate.gate_id]
            pooled.add(tally)
            automatic_recorded &= tally.automatic_recorded
            for pool, split in (
                (destinations, tally.destinations),
                (media, tally.media),
            ):
                for key, counts in split.items():
                    pool.setdefault(key, Split()).add(counts)
            entry.update(
                {
                    "fields": tally.fields,
                    "works": tally.works,
                    "by_destination": dict(sorted(tally.by_destination.items())),
                    "by_medium": dict(sorted(tally.by_medium.items())),
                    "true_positive": tally.true_positive,
                    "false_positive": tally.false_positive,
                    "unlabelled": tally.unlabelled,
                    "unreached_fields": tally.unreached,
                    "actionable_fields": tally.actionable
                    if estimable_control
                    else None,
                    "decidable_fields": tally.decidable if picks else None,
                    "pick_agrees": tally.pick_agrees if picks else None,
                    "tied_fields": tally.tied if picks else None,
                    "election_fields": tally.election_fields,
                    "automatic": _automatic_entry(tally, tally.automatic_recorded)
                    if picks
                    else None,
                }
            )
        per_snapshot.append(entry)
    total = pooled.true_positive + pooled.false_positive
    precision = pooled.true_positive / total if estimable_control and total else None
    lower = (
        wilson_lower_bound(pooled.true_positive, total) if estimable_control else None
    )
    score = (
        round(pooled.actionable * lower, 1)
        if gate.ranked and measurable_anywhere and lower is not None
        else 0.0
    )
    disposition = gate.disposition
    if gate.gate_id in follow_ons:
        disposition = f"filed as #{follow_ons[gate.gate_id]}"
    return {
        "gate_id": gate.gate_id,
        "family": gate.family,
        "gate": gate.gate,
        "location": gate.location,
        "origin": gate.origin,
        "origin_corpus": gate.corpus,
        "introduced_in": gate.introduced_in,
        "destinations": gate.destinations,
        "medium": gate.medium,
        "loosened_rule": gate.loosened,
        "control": gate.control,
        "snapshots": per_snapshot,
        "pooled": {
            "true_positive": pooled.true_positive,
            "false_positive": pooled.false_positive,
            "unlabelled": pooled.unlabelled,
            "n": total,
            "precision": None if precision is None else round(precision, 4),
            "wilson_lower_bound": None if lower is None else round(lower, 4),
        },
        "pick": _pick_entry(pooled) if picks and measurable_anywhere else None,
        "split": {
            "by_destination": {
                key: _split_entry(counts, automatic_recorded)
                for key, counts in sorted(destinations.items())
            },
            "by_medium": {
                key: _split_entry(counts, automatic_recorded)
                for key, counts in sorted(media.items())
            },
            "total": _split_entry(pooled, automatic_recorded),
        }
        if picks and measurable_anywhere
        else None,
        "ceiling": pooled.fields,
        "actionable": pooled.actionable if estimable_control else None,
        "score": score,
        "ranked": gate.ranked,
        "note": gate.unranked_note,
        "disposition": disposition,
        "proposed_follow_on": False,
    }


def _pick_entry(counts: Split) -> dict[str, object]:
    """The pick measure and the field classes of a decision-level row or split."""
    bound = wilson_lower_bound(counts.pick_agrees, counts.decidable)
    return {
        "decidable_fields": counts.decidable,
        "pick_agrees": counts.pick_agrees,
        "tied_fields": counts.tied,
        "contaminated_fields": counts.contaminated,
        "no_right_work_fields": counts.no_right_work,
        "single_alternative_fields": counts.single_alternative,
        "pick_preserves": counts.pick_preserves,
        "rate": round(counts.pick_agrees / counts.decidable, 4)
        if counts.decidable
        else None,
        "wilson_lower_bound": None if bound is None else round(bound, 4),
    }


def _automatic_entry(counts: Split, recorded: bool) -> dict[str, object] | None:
    """Blocked fields automatic decisions settled (#1747); None if not recorded."""
    if not recorded:
        return None
    return {
        "takes_pick": counts.automatic_pick,
        "keeps_current": counts.automatic_keep,
        "takes_other": counts.automatic_other,
    }


def _split_entry(counts: Split, automatic_recorded: bool) -> dict[str, object]:
    """One destination's or medium's controls and pick measure (#1744).

    Its automatic-decision counts are pooled only when every snapshot behind
    it recorded automatic decisions; otherwise they are "not recorded".
    """
    total = counts.true_positive + counts.false_positive
    lower = wilson_lower_bound(counts.true_positive, total)
    return {
        "fields": counts.fields,
        "actionable_fields": counts.actionable,
        "true_positive": counts.true_positive,
        "false_positive": counts.false_positive,
        "unlabelled": counts.unlabelled,
        "unreached_fields": counts.unreached,
        "n": total,
        "precision": round(counts.true_positive / total, 4) if total else None,
        "wilson_lower_bound": None if lower is None else round(lower, 4),
        "pick": _pick_entry(counts),
        "automatic": _automatic_entry(counts, automatic_recorded),
    }


def rank_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Scored rows by score; unscored by ceiling then ID; unranked rows last."""

    def score(row: Mapping[str, object]) -> float:
        return cast("float", row["score"])

    scored = sorted(
        (row for row in rows if row["ranked"] and score(row) > 0),
        key=lambda row: (-score(row), cast("str", row["gate_id"])),
    )
    unscored = sorted(
        (row for row in rows if row["ranked"] and score(row) <= 0),
        key=lambda row: (-cast("int", row["ceiling"]), cast("str", row["gate_id"])),
    )
    unranked = sorted(
        (row for row in rows if not row["ranked"]),
        key=lambda row: cast("str", row["gate_id"]),
    )
    ordered = [*scored, *unscored, *unranked]
    proposed = 0
    for rank, row in enumerate(ordered, start=1):
        row["rank"] = rank if row["ranked"] else None
        disposition = cast("str", row["disposition"])
        eligible = disposition == "kept" or disposition.startswith("filed as #")
        if proposed < 5 and eligible and score(row) > 0 and row["ranked"]:
            row["proposed_follow_on"] = True
            proposed += 1
    return ordered


_STRUCTURAL_KEYS = frozenset(
    {
        "schema",
        "snapshots",
        "rows",
        "label",
        "source_revision",
        "status",
        "fields",
        "works",
        "cases",
        "by_destination",
        "by_medium",
        "by_reason",
        "true_positive",
        "false_positive",
        "unlabelled",
        "unreached_fields",
        "actionable_fields",
        "actionable",
        "gate_id",
        "family",
        "gate",
        "location",
        "origin",
        "origin_corpus",
        "introduced_in",
        "destinations",
        "medium",
        "loosened_rule",
        "control",
        "pooled",
        "n",
        "precision",
        "wilson_lower_bound",
        "ceiling",
        "score",
        "ranked",
        "rank",
        "disposition",
        "proposed_follow_on",
        "attention",
        "media",
        "with_covers",
        "without_covers",
        "report_query_fields",
        "epub_inapplicable_fields",
        "source_report_reconciled",
        "current_cases",
        "wilson_z",
        "later_elections",
        "decidable_fields",
        "pick_agrees",
        "tied_fields",
        "election_fields",
        "pick",
        "rate",
        "note",
        "split",
        "total",
        "contaminated_fields",
        "no_right_work_fields",
        "single_alternative_fields",
        "pick_preserves",
        "breakdown",
        "automatic",
        "takes_pick",
        "keeps_current",
        "takes_other",
    }
)


def closed_vocabulary(labels: Iterable[str]) -> frozenset[str]:
    """Every string the output may contain besides validated revisions."""
    strings: set[str] = set(_STRUCTURAL_KEYS) | set(_ALL) | set(REASONS)
    strings |= {_OTHER_REASON, LEDGER_SCHEMA, *_MEDIA}
    strings |= {"measurable", "gate_postdates_source", "block_not_retained"}
    strings |= {"cause_not_separable", "informational", "rich_refusals_collapsed"}
    strings |= {label for label, _commit, _destinations in LATER_ELECTIONS}
    strings |= {"alternative", "cover_candidates", "not_identity", "not_retained"}
    for gate in CATALOGUE:
        strings |= {
            gate.gate_id,
            gate.family,
            gate.gate,
            gate.location,
            gate.origin,
            gate.corpus,
            gate.introduced_in,
            gate.destinations,
            gate.loosened,
            gate.disposition,
        }
        if gate.unranked_note is not None:
            strings.add(gate.unranked_note)
    strings |= set(labels)
    return frozenset(strings)


def _closed(value: object, vocabulary: frozenset[str]) -> None:
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if value in vocabulary or _REVISION.match(value):
            return
        if re.fullmatch(r"filed as #[1-9][0-9]{0,5}", value):
            return
        raise Refusal("output refused: unclosed string")
    if isinstance(value, dict):
        for key, item in cast("dict[object, object]", value).items():
            if not isinstance(key, str) or key not in vocabulary:
                raise Refusal("output refused: unclosed key")
            _closed(item, vocabulary)
        return
    if isinstance(value, list | tuple):
        for item in cast("Sequence[object]", value):
            _closed(item, vocabulary)
        return
    raise Refusal("output refused: unclosed value")


def _engine(probe: AttentionProbe) -> tuple[frozenset[str], bool]:
    """The source's scalar destinations and rich-reason split, as its probe states."""
    if probe.scalar is None:
        scalar = frozenset(shadow_decision._SUPPORTED_DESTINATIONS)
    else:
        scalar = frozenset(probe.scalar)
    if not scalar <= _ALL:
        raise Refusal("attention probe returned an unknown vocabulary")
    return scalar, True if probe.rich_split is None else probe.rich_split


def _a6_module() -> ModuleType:
    """Import the A6 analysis, which imports this module back (#1745)."""
    try:
        from scripts import admission_rule_a6 as module
    except ImportError:  # pragma: no cover - direct `python scripts/...` execution
        sys.modules.setdefault("admission_rule_ledger", sys.modules[__name__])
        import admission_rule_a6 as module  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    return module


def _a3_module() -> ModuleType:
    """Import the A3 analysis, which imports this module back (#1746)."""
    try:
        from scripts import admission_rule_a3 as module
    except ImportError:  # pragma: no cover - direct `python scripts/...` execution
        sys.modules.setdefault("admission_rule_ledger", sys.modules[__name__])
        import admission_rule_a3 as module  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    return module


def build_ledger(
    inputs: Sequence[SnapshotInput],
    *,
    ancestry: Ancestry,
    attention: AttentionReader,
    a6_replay: A6Reader | None = None,
    a3_replay: A3Reader | None = None,
    follow_ons: Mapping[str, int] | None = None,
    breakdown: str | None = None,
) -> dict[str, object]:
    measured: list[tuple[str, Mapping[str, Status], Mapping[str, Tally]]] = []
    snapshot_entries: list[dict[str, object]] = []
    a6 = None if a6_replay is None else _a6_module()
    a6_entries: list[dict[str, object]] = []
    a6_tallies: list[object] = []
    a3 = None if a3_replay is None else _a3_module()
    a3_entries: list[dict[str, object]] = []
    a3_tallies: list[object] = []
    for item in inputs:
        connection = _open_snapshot(item.database)
        try:
            # The source probe states the engine split the A6 rows need.
            probe = attention(item.source_root, item.database)
            scalar, rich_split = _engine(probe)
            statuses = gate_statuses(item.revision, ancestry, rich_split)
            elections = later_elections(item.revision, ancestry)
            tallies, media = measure_snapshot(connection, statuses, elections, scalar)
        finally:
            connection.close()
        if a6 is not None and a6_replay is not None:
            replay = a6_replay(item.source_root, item.database)
            if replay.scalar != scalar or replay.rich_split != rich_split:
                raise Refusal("decision replay disagrees with the source probe")
            connection = _open_snapshot(item.database)
            try:
                a6_tally, _media = a6.measure_a6(connection, replay)
                decisions = a6.human_decisions(connection)
            finally:
                connection.close()
            a6_entries.append(
                a6.snapshot_entry(item.label, replay, a6_tally, decisions)
            )
            a6_tallies.append(a6_tally)
        if a3 is not None and a3_replay is not None:
            combination = a3_replay(item.source_root, item.database)
            connection = _open_snapshot(item.database)
            try:
                a3_tally = a3.measure_a3(
                    connection, combination, _election_destinations(elections)
                )
                decisions = a3.human_decisions(connection)
            finally:
                connection.close()
            a3_entries.append(
                a3.snapshot_entry(
                    item.label,
                    combination,
                    a3_tally,
                    withheld_title_rule=ancestry(a3.WITHHELD_TITLE_RULE, item.revision),
                    human_decisions=decisions,
                )
            )
            a3_tallies.append(a3_tally)
        if len(probe.rows) != probe.report_rows_excluded:
            raise Refusal("attention probe disagrees with the source's report query")
        summary = summarise_attention(probe)
        reconciled: bool | None = None
        if item.source_report is not None:
            try:
                report = _dict(
                    json.loads(item.source_report.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                raise Refusal("source report unreadable") from None
            reconciled = reconcile_report(summary, report)
            if not reconciled:
                raise Refusal(
                    "attention does not reconcile with the source report", code=1
                )
        snapshot_entries.append(
            {
                "label": item.label,
                "source_revision": item.revision,
                "current_cases": dict(sorted(media.items())),
                "later_elections": list(elections),
                "attention": summary,
                "source_report_reconciled": reconciled,
            }
        )
        measured.append((item.label, statuses, tallies))
    rows = rank_rows([_row(gate, measured, follow_ons or {}) for gate in CATALOGUE])
    ledger: dict[str, object] = {
        "schema": LEDGER_SCHEMA,
        "wilson_z": WILSON_Z,
        "snapshots": snapshot_entries,
        "rows": rows,
        "breakdown": breakdown,
    }
    vocabulary = closed_vocabulary(item.label for item in inputs)
    if a6 is not None:
        section: dict[str, object] = {
            "snapshots": a6_entries,
            "pooled_relaxations": a6.pooled_relaxations(a6_tallies),
        }
        ledger[a6.KEY] = section
        vocabulary |= a6.vocabulary(section)
    if a3 is not None:
        combined: dict[str, object] = {
            "snapshots": a3_entries,
            "pooled_relaxations": a3.pooled_relaxations(a3_tallies),
        }
        ledger[a3.KEY] = combined
        vocabulary |= a3.vocabulary(combined)
    _closed(ledger, vocabulary)
    return ledger


# ---------------------------------------------------------------------------
# Markdown rendering of the validated ledger.


def _number(value: object) -> str:
    return f"{cast('int', value):,}"


def _reach(fields: int) -> str:
    return (
        "1 field reaches no record"
        if fields == 1
        else f"{_number(fields)} fields reach no record"
    )


def _cell(entry: Mapping[str, object]) -> str:
    status = cast("str", entry["status"])
    if status != "measurable":
        return _STATUS_TEXT[status]
    cell = f"{_number(entry['fields'])} / {_number(entry['works'])}"
    notes = []
    if entry.get("actionable_fields") is not None:
        notes.append(f"{_number(entry['actionable_fields'])} actionable")
    if entry.get("election_fields"):
        notes.append(f"{_number(entry['election_fields'])} stale: later election")
    automatic = entry.get("automatic")
    if isinstance(automatic, dict) and (
        decided := sum(cast("dict[str, int]", automatic).values())
    ):
        notes.append(f"{_number(decided)} decided automatically")
    return f"{cell} ({'; '.join(notes)})" if notes else cell


def _controls(row: Mapping[str, object]) -> str:
    control = cast("str", row["control"])
    if control in _CONTROL_TEXT:
        return _CONTROL_TEXT[control]
    parts = [
        f"{entry['label']} {entry['true_positive']}/{entry['false_positive']}/"
        f"{entry['unlabelled']} ({_reach(cast('int', entry['unreached_fields']))})"
        for entry in cast("list[dict[str, object]]", row["snapshots"])
        if entry["status"] == "measurable"
    ]
    if not parts:
        return "not estimable: not measurable on either snapshot"
    pooled = _dict(row["pooled"])
    return "; ".join(parts) + f"; n = {pooled['n']}"


def _precision(estimate: Mapping[str, object]) -> str:
    if estimate["precision"] is None or estimate["wilson_lower_bound"] is None:
        return "not estimable"
    return f"{cast('float', estimate['precision']):.3f} ({cast('float', estimate['wilson_lower_bound']):.3f})"


def _agreement(pick: Mapping[str, object]) -> str | None:
    if pick["rate"] is None or pick["wilson_lower_bound"] is None:
        return None
    return (
        f"{_number(pick['pick_agrees'])}/{_number(pick['decidable_fields'])} "
        f"{cast('float', pick['rate']):.3f} "
        f"({cast('float', pick['wilson_lower_bound']):.3f})"
    )


def _pick_cell(row: Mapping[str, object]) -> str:
    pick = row.get("pick")
    if not isinstance(pick, dict):
        return "not applicable"
    agreement = _agreement(cast("dict[str, object]", pick))
    tied = f"{_number(pick['tied_fields'])} tied"
    if agreement is None:
        return f"not estimable (0 decidable; {tied})"
    return f"{agreement}; {tied}"


def _breakdown_section(row: Mapping[str, object]) -> list[str]:
    """One decision-level row's controls and field classes per destination and medium."""
    lines = ["", f"### Breakdown: {row['gate_id']}", ""]
    split = row.get("split")
    if not isinstance(split, dict):
        return [*lines, "Not measurable on either snapshot."]
    lines += [
        "| Split | Fields | Actionable | TP/FP/unlabelled | Reach no record | "
        "Identity precision (lower) | Pick agrees (lower) | Tied | Contaminated | "
        "No right-work | Single | Pick preserves | Automatic pick/keep/other |",
        "|" + "---|" * 13,
    ]
    groups = [
        (key if basis == "destination" else f"medium: {key}", _dict(entry))
        for basis in ("destination", "medium")
        for key, entry in _dict(_dict(split)[f"by_{basis}"]).items()
    ]
    groups.append(("total", _dict(_dict(split)["total"])))
    for name, entry in groups:
        pick = _dict(entry["pick"])
        automatic = entry.get("automatic")
        settled = (
            "/".join(
                _number(cast("dict[str, object]", automatic)[key])
                for key in ("takes_pick", "keeps_current", "takes_other")
            )
            if isinstance(automatic, dict)
            else _NOT_RECORDED
        )
        lines.append(
            f"| {name} | {_number(entry['fields'])} | "
            f"{_number(entry['actionable_fields'])} | "
            f"{entry['true_positive']}/{entry['false_positive']}/{entry['unlabelled']} | "
            f"{_number(entry['unreached_fields'])} | {_precision(entry)} | "
            f"{_agreement(pick) or 'not estimable'} | {_number(pick['tied_fields'])} | "
            f"{_number(pick['contaminated_fields'])} | "
            f"{_number(pick['no_right_work_fields'])} | "
            f"{_number(pick['single_alternative_fields'])} | "
            f"{_number(pick['pick_preserves'])} | {settled} |"
        )
    return lines


def render_markdown(ledger: Mapping[str, object]) -> str:
    """The ledger's tables, or with --a6-abstention, that analysis's tables only."""
    if "a6_abstention" in ledger:
        a6 = _a6_module()
        return "\n".join(a6.render(_dict(ledger[a6.KEY]))) + "\n"
    if "a3_combination" in ledger:
        a3 = _a3_module()
        return "\n".join(a3.render(_dict(ledger[a3.KEY]))) + "\n"
    snapshots = cast("list[dict[str, object]]", ledger["snapshots"])
    labels = [cast("str", entry["label"]) for entry in snapshots]
    header = (
        "| Rank | ID | Gate | Fields | Origin | Origin corpus / precision | "
        + " | ".join(f"{label} fields / works" for label in labels)
        + " | Loosened rule | Controls TP/FP/unlabelled | "
        "Identity precision (Wilson 95% lower) | "
        "Pick agrees where controls decide (Wilson 95% lower) | Score | Disposition |"
    )
    lines = [
        "### Ranked gates",
        "",
        header,
        "|" + "---|" * (12 + len(labels)),
    ]
    for row in cast("list[dict[str, object]]", ledger["rows"]):
        rank = row.get("rank")
        disposition = cast("str", row["disposition"])
        if row["proposed_follow_on"] and not disposition.startswith("filed as #"):
            disposition = "proposed follow-on"
        cells = [
            str(rank)
            if rank is not None
            else ("unranked" if row["note"] else "out of scope"),
            cast("str", row["gate_id"]),
            cast("str", row["gate"]) + (f" ({row['note']})" if row["note"] else ""),
            cast("str", row["destinations"])
            + ("" if row["medium"] is None else f" ({row['medium']} only)"),
            cast("str", row["origin"]),
            cast("str", row["origin_corpus"]),
            *(
                _cell(entry)
                for entry in cast("list[dict[str, object]]", row["snapshots"])
            ),
            cast("str", row["loosened_rule"]),
            _controls(row),
            _precision(_dict(row["pooled"])),
            _pick_cell(row),
            f"{cast('float', row['score']):,.1f}",
            disposition,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    for entry in snapshots:
        elections = cast("list[str]", entry["later_elections"])
        lines.append(
            f"- {entry['label']}: source predates "
            + (", ".join(elections) if elections else "no later election")
            + "."
        )
    lines += [
        "",
        "### Attention per case",
        "",
        "| Snapshot | Medium | Fields with covers | Cases with covers | "
        "Fields without covers | Cases without covers |",
        "|---|---|---|---|---|---|",
    ]
    for entry in snapshots:
        attention = _dict(entry["attention"])
        for medium in _MEDIA:
            bases = _dict(_dict(attention["media"])[medium])
            with_covers, without = (
                _dict(bases["with_covers"]),
                _dict(bases["without_covers"]),
            )
            lines.append(
                f"| {entry['label']} | {medium} | {_number(with_covers['fields'])} | "
                f"{_number(with_covers['cases'])} | {_number(without['fields'])} | "
                f"{_number(without['cases'])} |"
            )
    lines += ["", "| Snapshot | Destination | Attention fields |", "|---|---|---|"]
    for entry in snapshots:
        for destination, count in _dict(
            _dict(entry["attention"])["by_destination"]
        ).items():
            lines.append(f"| {entry['label']} | {destination} | {_number(count)} |")
    lines += ["", "| Snapshot | Reason | Attention fields |", "|---|---|---|"]
    for entry in snapshots:
        for reason, count in _dict(_dict(entry["attention"])["by_reason"]).items():
            lines.append(f"| {entry['label']} | {reason} | {_number(count)} |")
    if ledger.get("breakdown") is not None:
        lines += _breakdown_section(
            next(
                row
                for row in cast("list[dict[str, object]]", ledger["rows"])
                if row["gate_id"] == ledger["breakdown"]
            )
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Command line.


class _Parser(argparse.ArgumentParser):
    """Refuses bad arguments with a fixed line; never echoes what was passed."""

    def error(self, message: str) -> NoReturn:
        del message
        raise Refusal("invalid arguments")


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        action="append",
        nargs=3,
        required=True,
        metavar=("LABEL", "DATABASE", "SOURCE_REVISION"),
    )
    parser.add_argument(
        "--source-root",
        action="append",
        nargs=2,
        default=[],
        metavar=("LABEL", "DIRECTORY"),
    )
    parser.add_argument(
        "--source-report",
        action="append",
        nargs=2,
        default=[],
        metavar=("LABEL", "FILE"),
    )
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--a6-abstention", action="store_true")
    parser.add_argument("--a3-combination", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument(
        "--follow-on", action="append", default=[], metavar="GATE=ISSUE"
    )
    parser.add_argument("--breakdown", default=None, metavar="GATE")
    return parser.parse_args(argv)


def _inputs(arguments: argparse.Namespace) -> list[SnapshotInput]:
    snapshots = cast("list[list[str]]", arguments.snapshot)
    roots = cast("list[list[str]]", arguments.source_root)
    reports = cast("list[list[str]]", arguments.source_report)
    every_label = [entry[0] for entry in (*snapshots, *roots, *reports)]
    if any(not _LABEL.match(label) for label in every_label):
        raise Refusal("invalid label")
    labels = [entry[0] for entry in snapshots]
    if len(set(labels)) != len(labels):
        raise Refusal("duplicate label")
    root_by_label = dict((entry[0], entry[1]) for entry in roots)
    report_by_label = dict((entry[0], entry[1]) for entry in reports)
    if set(root_by_label) != set(labels) or not set(report_by_label) <= set(labels):
        raise Refusal("every snapshot needs exactly one source root")
    inputs: list[SnapshotInput] = []
    for label, database, revision in snapshots:
        if not _REVISION.match(revision):
            raise Refusal("source revision must be a full commit")
        report = report_by_label.get(label)
        inputs.append(
            SnapshotInput(
                label,
                Path(database),
                revision,
                Path(root_by_label[label]),
                None if report is None else Path(report),
            )
        )
    return inputs


def _breakdown_gate(value: str) -> Gate:
    """The decision-level gate a breakdown is asked for; refuses anything else."""
    for gate in CATALOGUE:
        if gate.gate_id == value and gate.control == "alternative":
            return gate
    raise Refusal("invalid breakdown")


def _follow_ons(values: Sequence[str]) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for value in values:
        gate_id, _, issue = value.partition("=")
        if (
            not _GATE_ID.match(gate_id)
            or gate_id not in {gate.gate_id for gate in CATALOGUE}
            or not re.fullmatch(r"[1-9][0-9]{0,5}", issue)
        ):
            raise Refusal("invalid follow-on")
        parsed[gate_id] = int(issue)
    return parsed


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    ancestry: Ancestry | None = None,
    attention: AttentionReader | None = None,
    decision_replay: A6Reader | None = None,
    combination_replay: A3Reader | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        arguments = _parse(argv)
        inputs = _inputs(arguments)
        follow_ons = _follow_ons(cast("list[str]", arguments.follow_on))
        breakdown = cast("str | None", arguments.breakdown)
        if breakdown is not None:
            _breakdown_gate(breakdown)
        for item in inputs:
            if not item.database.is_file():
                raise Refusal("database unreadable")
        a6_replay: A6Reader | None = None
        if arguments.a6_abstention:
            a6_replay = (
                cast("A6Reader", _a6_module().child_a6)
                if decision_replay is None
                else decision_replay
            )
        a3_replay: A3Reader | None = None
        if arguments.a3_combination:
            a3_replay = (
                cast("A3Reader", _a3_module().child_a3)
                if combination_replay is None
                else combination_replay
            )
        ledger = build_ledger(
            inputs,
            ancestry=git_ancestry(arguments.repository)
            if ancestry is None
            else ancestry,
            attention=child_attention if attention is None else attention,
            a6_replay=a6_replay,
            a3_replay=a3_replay,
            follow_ons=follow_ons,
            breakdown=breakdown,
        )
        if arguments.format == "markdown":
            rendered = render_markdown(ledger)
        else:
            rendered = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    except Refusal as refusal:
        errors.write(f"admission-rule-ledger: {refusal.message}\n")
        return refusal.code
    except Exception:  # A surprising document shape: no traceback, no value.
        errors.write("admission-rule-ledger: replay failed\n")
        return 2
    output.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
