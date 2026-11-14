"""Offline, read-only replay of the measured cover rules (#1363, #1362).

Usage: ``uv run python scripts/replay_cover_rules.py INPUT.json``

The input is a ``cover-rules-replay-input-v2`` document of cover fields, each
with its medium, its current cover (or ``null`` for a known-empty target) and
its identity-eligible candidates. Fields carry no identifier: candidates are
named by position. A current cover states its ``read`` (``readable``,
``unreadable`` or ``unknown``, ADR-0094) and its ``identity`` (a verified tier
or ``null``), and may state ``retainable`` (by default a readable cover is and
an unreadable one is not) and ``contradicted`` (by default not); only a
readable one carries dimensions and measures. A v1 document is refused. The
document's ``persisted`` list names which optional inputs the source kept:

- ``same_art_measures``: decoded-pixel measures for the same-art rule;
- ``edition_language``: the non-English edition flag;
- ``omnibus``: the book's omnibus membership and each candidate's member flag;
- ``title``: the title conflict and each candidate's title disagreement;
- ``provider_art``: each field's ``matches_provider_art``, whether its current
  cover matches any provider image.

A rule whose input is not persisted is reported ``not_replayable`` and runs
with that input absent, so the outcome counts show what the replayable rules
decide alone; nothing is inferred. A field carrying an input the document does
not declare is refused. Shape and the floor read dimensions, which every field
carries.

The output is aggregates only: outcome counts per protection setting (with and
without the edition option) and medium; the same counts split by the reason
recorded on each decision (a closed issue, ``edition_preference`` or ``none``),
so a kept fault and a switched fault are counted apart; issue and exclusion
counts; the number of images same-art collapse removed; counts by current
class (confirmed, unconfirmed, contradicted, unreadable, unknown, known-empty);
the would-change counts (replace, suggest, hold) per setting and class; and
provider-art matches (``not_replayable`` when not persisted). No reference,
value or title from the input is written.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, TextIO

from mechanical_librarian.domain.cover_rules import (
    MEDIA,
    CoverExclusion,
    CoverIssue,
    CoverMeasures,
    CoverOutcome,
    CoverProtection,
    CoverRuleCandidate,
    CurrentCover,
    CurrentCoverRead,
    elect_measured_covers,
)
from mechanical_librarian.domain.validation import boolean, literal, one_of

INPUT_SCHEMA = "cover-rules-replay-input-v2"
OUTPUT_SCHEMA = "cover-rules-replay-v2"
OPTIONAL_INPUTS = (
    "same_art_measures",
    "edition_language",
    "omnibus",
    "title",
    "provider_art",
)
CURRENT_CLASSES = (
    "confirmed",
    "unconfirmed",
    "contradicted",
    "unreadable",
    "unknown",
    "known_empty",
)
CHANGES = (CoverOutcome.REPLACE, CoverOutcome.SUGGEST, CoverOutcome.HOLD)
# Each rule and the optional input it needs; shape and floor need dimensions only.
RULE_INPUTS: Mapping[str, str | None] = {
    "shape": None,
    "floor": None,
    "same_art": "same_art_measures",
    "edition_language": "edition_language",
    "omnibus": "omnibus",
    "title": "title",
}
# Optional keys of a field, a candidate and a current cover, by input.
_FIELD_KEYS = {
    "title_conflict": "title",
    "omnibus": "omnibus",
    "matches_provider_art": "provider_art",
}
_CANDIDATE_KEYS = {
    "measures": "same_art_measures",
    "edition_outside_library_languages": "edition_language",
    "omnibus_volume": "omnibus",
    "title_differs": "title",
}
_CURRENT_KEYS = {
    "measures": "same_art_measures",
    "edition_outside_library_languages": "edition_language",
}


class Refusal(Exception):
    """A refused input, reported with a fixed message and never its values."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _object(
    value: object, required: set[str], optional: set[str] | frozenset[str] = frozenset()
) -> dict[str, object]:
    # Local because these documents have optional keys, which the shared
    # ``closed_document`` (an exact key set) does not express.
    if type(value) is not dict or not required <= value.keys() <= required | optional:
        raise Refusal("input shape is invalid")
    return value


def _declared(
    value: dict[str, object], optional: Mapping[str, str], persisted: set[str]
) -> None:
    if any(optional[key] not in persisted for key in value.keys() & optional.keys()):
        raise Refusal("input carries an undeclared input")


def _flag(value: dict[str, object], key: str) -> bool:
    flag = value.get(key, False)
    if not boolean(flag):
        raise Refusal("input shape is invalid")
    return flag is True


def _measures(value: dict[str, object]) -> CoverMeasures | None:
    raw = value.get("measures")
    # The report's own reader, so a replay reads measures exactly as stored.
    return None if raw is None else CoverMeasures.from_data(raw)


def _current_class(current: CurrentCover | None) -> str:
    if current is None:
        return "known_empty"
    if current.contradicted:
        return "contradicted"
    if current.read is CurrentCoverRead.READABLE:
        return "unconfirmed" if current.identity is None else "confirmed"
    return current.read.value


def _current(data: dict[str, object]) -> CurrentCover:
    read = CurrentCoverRead(data["read"])
    return CurrentCover(
        "current",
        data["identity"],  # type: ignore[arg-type]  # reason: CurrentCover refuses other values
        read=read,
        retainable=data.get("retainable", read is CurrentCoverRead.READABLE),  # type: ignore[arg-type]  # reason: as above
        width=data.get("width"),  # type: ignore[arg-type]  # reason: as above
        height=data.get("height"),  # type: ignore[arg-type]  # reason: as above
        measures=_measures(data),
        edition_outside_library_languages=_flag(
            data, "edition_outside_library_languages"
        ),
        contradicted=data.get("contradicted", False),  # type: ignore[arg-type]  # reason: as above
    )


def _field(
    value: object, persisted: set[str]
) -> tuple[
    str, CurrentCover | None, tuple[CoverRuleCandidate, ...], bool, bool, bool | None
]:
    field = _object(value, {"medium", "current", "candidates"}, set(_FIELD_KEYS))
    _declared(field, _FIELD_KEYS, persisted)
    medium, raw_current, raw_candidates = (
        field["medium"],
        field["current"],
        field["candidates"],
    )
    if medium not in MEDIA or type(raw_candidates) is not list:
        raise Refusal("input shape is invalid")
    current = None
    if raw_current is not None:
        data = _object(
            raw_current,
            {"read", "identity"},
            {"width", "height", "retainable", "contradicted", *_CURRENT_KEYS},
        )
        _declared(data, _CURRENT_KEYS, persisted)
        current = _current(data)
    matches = field.get("matches_provider_art")
    # Only a current cover can match provider art.
    if (matches is not None and not boolean(matches)) or (
        "matches_provider_art" in field and current is None
    ):
        raise Refusal("input shape is invalid")
    candidates = []
    for index, raw in enumerate(raw_candidates):
        data = _object(
            raw, {"identity", "width", "height", "packet_ordinal"}, set(_CANDIDATE_KEYS)
        )
        _declared(data, _CANDIDATE_KEYS, persisted)
        candidates.append(
            CoverRuleCandidate(
                f"candidate-{index}",
                data["identity"],  # type: ignore[arg-type]  # reason: CoverRuleCandidate refuses other values
                data["width"],  # type: ignore[arg-type]  # reason: as above
                data["height"],  # type: ignore[arg-type]  # reason: as above
                data["packet_ordinal"],  # type: ignore[arg-type]  # reason: as above
                measures=_measures(data),
                edition_outside_library_languages=_flag(
                    data, "edition_outside_library_languages"
                ),
                omnibus_volume=_flag(data, "omnibus_volume"),
                title_differs=_flag(data, "title_differs"),
            )
        )
    return (
        medium,
        current,
        tuple(candidates),
        _flag(field, "title_conflict"),
        _flag(field, "omnibus"),
        matches if matches is None else matches is True,
    )


def replay(document: object) -> dict[str, object]:
    """Replay every field under each setting and return aggregates only."""
    root = _object(document, {"schema", "persisted", "fields"})
    persisted_list, fields = root["persisted"], root["fields"]
    if (
        not literal(INPUT_SCHEMA)(root["schema"])
        or type(persisted_list) is not list
        or not all(one_of(*OPTIONAL_INPUTS)(item) for item in persisted_list)
        or len(set(persisted_list)) != len(persisted_list)
        or type(fields) is not list
    ):
        raise Refusal("input shape is invalid")
    persisted = set(persisted_list)
    try:
        parsed = [_field(value, persisted) for value in fields]
    except ValueError:
        raise Refusal("input values are invalid") from None

    settings = [
        (protection.value + ("+prefer_edition" if prefer else ""), protection, prefer)
        for prefer in (False, True)
        for protection in CoverProtection
    ]
    outcomes = {
        name: {medium: Counter[str]() for medium in MEDIA} for name, _, _ in settings
    }
    issues = {medium: Counter[str]() for medium in MEDIA}
    reasons = {
        name: {medium: Counter[tuple[str, str]]() for medium in MEDIA}
        for name, _, _ in settings
    }
    exclusions = {medium: Counter[str]() for medium in MEDIA}
    collapsed = Counter[str]()
    classes = {medium: Counter[str]() for medium in MEDIA}
    changes = {
        name: {c: Counter[str]() for c in CURRENT_CLASSES} for name, *_ in settings
    }
    provider_art = {medium: Counter[str]() for medium in MEDIA}
    unmeasured = 0
    for medium, current, candidates, title_conflict, omnibus, matches in parsed:
        current_class = _current_class(current)
        classes[medium][current_class] += 1
        if matches is not None:
            provider_art[medium]["matches" if matches else "no_match"] += 1
        unmeasured += sum(1 for c in candidates if c.measures is None) + (
            1
            if current is not None
            and current.read is CurrentCoverRead.READABLE
            and current.measures is None
            else 0
        )
        for index, (name, protection, prefer) in enumerate(settings):
            election = elect_measured_covers(
                candidates,
                medium=medium,
                current=current,
                protection=protection,
                prefer_edition=prefer,
                title_conflict=title_conflict,
                omnibus=omnibus,
            )
            outcomes[name][medium][election.outcome.value] += 1
            if election.outcome in CHANGES:
                changes[name][current_class][election.outcome.value] += 1
            reason = (
                election.reason.value
                if election.reason is not None
                else "edition_preference"
                if election.edition_preferred
                else "none"
            )
            reasons[name][medium][(election.outcome.value, reason)] += 1
            if index == 0:
                issues[medium].update(issue.value for issue in election.issues)
                exclusions[medium].update(
                    reason.value for _, reason in election.excluded
                )
                collapsed[medium] += sum(len(g.members) - 1 for g in election.groups)

    def table(counter: Counter[str], vocabulary: Sequence[str]) -> dict[str, int]:
        return {key: counter[key] for key in vocabulary}

    return {
        "schema": OUTPUT_SCHEMA,
        "fields": {
            medium: sum(1 for item in parsed if item[0] == medium) for medium in MEDIA
        },
        "rules": {
            rule: "replayed"
            if needed is None or needed in persisted
            else "not_replayable"
            for rule, needed in RULE_INPUTS.items()
        },
        "outcomes": {
            name: {
                medium: table(counter, [o.value for o in CoverOutcome])
                for medium, counter in by_medium.items()
            }
            for name, by_medium in outcomes.items()
        },
        "reasons": {
            name: {
                medium: {
                    outcome.value: {
                        reason: count
                        for (decided, reason), count in sorted(counter.items())
                        if decided == outcome.value
                    }
                    for outcome in CoverOutcome
                }
                for medium, counter in by_medium.items()
            }
            for name, by_medium in reasons.items()
        },
        "issues": {
            medium: table(counter, [i.value for i in CoverIssue])
            for medium, counter in issues.items()
        },
        "exclusions": {
            medium: table(counter, [e.value for e in CoverExclusion])
            for medium, counter in exclusions.items()
        },
        "collapsed_images": {medium: collapsed[medium] for medium in MEDIA},
        "images_without_measures": unmeasured,
        "current_classes": {
            medium: table(counter, CURRENT_CLASSES)
            for medium, counter in classes.items()
        },
        "would_change": {
            name: {
                current_class: table(counter, [c.value for c in CHANGES])
                for current_class, counter in by_class.items()
            }
            for name, by_class in changes.items()
        },
        "provider_art": {
            medium: table(counter, ("matches", "no_match"))
            if "provider_art" in persisted
            else "not_replayable"
            for medium, counter in provider_art.items()
        },
    }


class _Parser(argparse.ArgumentParser):
    """Refuses bad arguments with a fixed line; never echoes what was passed."""

    def error(self, message: str) -> NoReturn:
        del message
        raise Refusal("invalid arguments")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("input", type=Path)
        arguments = parser.parse_args(argv)
        try:
            document = json.loads(Path(arguments.input).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refusal("input unreadable") from None
        rendered = json.dumps(replay(document), indent=2, sort_keys=True) + "\n"
    except Refusal as refusal:
        errors.write(f"replay-cover-rules: {refusal.message}\n")
        return 2
    output.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
