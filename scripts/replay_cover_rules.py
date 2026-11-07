"""Offline, read-only replay of the measured cover rules (#1363, ADR-0093).

Usage: ``uv run python scripts/replay_cover_rules.py INPUT.json``

The input is a ``cover-rules-replay-input-v1`` document of cover fields, each
with its medium, its current cover (or ``null`` for a known-empty target) and
its identity-eligible candidates. Fields carry no identifier: candidates are
named by position. The document's ``persisted`` list names which optional
inputs the source kept:

- ``same_art_measures``: decoded-pixel measures for the same-art rule;
- ``edition_language``: the non-English edition flag;
- ``omnibus``: the book's omnibus membership and each candidate's member flag;
- ``title``: the title conflict and each candidate's title disagreement.

A rule whose input is not persisted is reported ``not_replayable`` and runs
with that input absent, so the outcome counts show what the replayable rules
decide alone; nothing is inferred. A field carrying an input the document does
not declare is refused. Shape and the floor read dimensions, which every field
carries.

The output is aggregates only: outcome counts per protection setting (with and
without the edition option) and medium, issue and exclusion counts, and the
number of images same-art collapse removed. No reference, value or title from
the input is written.
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
    elect_measured_covers,
)

INPUT_SCHEMA = "cover-rules-replay-input-v1"
OUTPUT_SCHEMA = "cover-rules-replay-v1"
OPTIONAL_INPUTS = ("same_art_measures", "edition_language", "omnibus", "title")
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
_FIELD_KEYS = {"title_conflict": "title", "omnibus": "omnibus"}
_CANDIDATE_KEYS = {
    "measures": "same_art_measures",
    "non_english_edition": "edition_language",
    "omnibus_volume": "omnibus",
    "title_differs": "title",
}
_CURRENT_KEYS = {
    "measures": "same_art_measures",
    "non_english_edition": "edition_language",
}


class Refusal(Exception):
    """A refused input, reported with a fixed message and never its values."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _object(
    value: object, required: set[str], optional: set[str] | frozenset[str] = frozenset()
) -> dict[str, object]:
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
    if type(flag) is not bool:
        raise Refusal("input shape is invalid")
    return flag


def _measures(value: dict[str, object]) -> CoverMeasures | None:
    raw = value.get("measures")
    if raw is None:
        return None
    data = _object(raw, {"phash", "dhash", "colour", "layout"})
    colour, layout = data["colour"], data["layout"]
    if type(colour) is not list or type(layout) is not list:
        raise Refusal("input shape is invalid")
    return CoverMeasures(
        data["phash"],  # type: ignore[arg-type]  # reason: CoverMeasures refuses a non-str
        data["dhash"],  # type: ignore[arg-type]  # reason: as above
        tuple(colour),
        tuple(layout),
    )


def _field(
    value: object, persisted: set[str]
) -> tuple[str, CurrentCover | None, tuple[CoverRuleCandidate, ...], bool, bool]:
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
            raw_current, {"readable", "identity"}, {"width", "height", *_CURRENT_KEYS}
        )
        _declared(data, _CURRENT_KEYS, persisted)
        current = CurrentCover(
            "current",
            data["identity"],  # type: ignore[arg-type]  # reason: CurrentCover refuses other values
            readable=data["readable"],  # type: ignore[arg-type]  # reason: as above
            width=data.get("width"),  # type: ignore[arg-type]  # reason: as above
            height=data.get("height"),  # type: ignore[arg-type]  # reason: as above
            measures=_measures(data),
            non_english_edition=_flag(data, "non_english_edition"),
        )
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
                non_english_edition=_flag(data, "non_english_edition"),
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
    )


def replay(document: object) -> dict[str, object]:
    """Replay every field under each setting and return aggregates only."""
    root = _object(document, {"schema", "persisted", "fields"})
    persisted_list, fields = root["persisted"], root["fields"]
    if (
        root["schema"] != INPUT_SCHEMA
        or type(persisted_list) is not list
        or any(item not in OPTIONAL_INPUTS for item in persisted_list)
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
    exclusions = {medium: Counter[str]() for medium in MEDIA}
    collapsed = Counter[str]()
    unmeasured = 0
    for medium, current, candidates, title_conflict, omnibus in parsed:
        unmeasured += sum(1 for c in candidates if c.measures is None) + (
            1
            if current is not None and current.readable and current.measures is None
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
