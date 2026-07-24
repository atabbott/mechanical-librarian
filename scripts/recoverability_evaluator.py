#!/usr/bin/env python3
"""Aggregate-only recoverability classification for private EPUB qualification."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any


SCHEMA = "recoverability-evaluator-v1"
CATEGORIES = (
    "absent_in_source", "inspection_failure", "extracted_not_projected",
    "normalization_mismatch", "semantic_mismatch", "matched_partial", "matched_complete",
)
ISBN_RELATIONS = ("accepted", "associated", "unrelated", "invalid", "unresolved")
NON_VALUE_STATES = {"unknown", "known_empty", "not_applicable", "accepted_unsupported", "unexplained_failure"}


def category(row: dict[str, Any]) -> str:
    if row["inspection"] == "failure":
        return "inspection_failure"
    if row["source"] == "absent":
        return "absent_in_source"
    if row["extraction"] == "not_found" or row["projection"] == "not_projected":
        return "extracted_not_projected"
    comparison = row["comparison"]
    if comparison == "normalization_mismatch":
        return comparison
    if comparison == "semantic_mismatch":
        return comparison
    if comparison == "partial":
        return "matched_partial"
    if comparison == "complete":
        return "matched_complete"
    raise ValueError("value row has no closed recoverability category")


def evaluate(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema") != SCHEMA or not isinstance(document.get("rows"), list):
        raise ValueError("invalid recoverability input")
    categories: Counter[str] = Counter()
    by_field: dict[str, Counter[str]] = defaultdict(Counter)
    excluded: Counter[str] = Counter()
    exploratory: Counter[str] = Counter()
    isbn: Counter[str] = Counter()
    denominator = 0
    for row in document["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("field"), str):
            raise ValueError("invalid recoverability row")
        state = row.get("state")
        if state in NON_VALUE_STATES:
            excluded[str(state)] += 1
            continue
        if state != "value" or type(row.get("supported")) is not bool or type(row.get("readable")) is not bool:
            raise ValueError("invalid recoverability row state")
        result = category(row)
        if row["field"] in {"edition", "rights"}:
            if row["supported"]:
                raise ValueError("edition and rights are exploratory only")
            exploratory[result] += 1
        elif row["supported"]:
            denominator += 1
            categories[result] += 1
            by_field[row["field"]][result] += 1
        else:
            excluded["accepted_unsupported"] += 1
        relation = row.get("isbn_relation")
        if relation is not None:
            if row["field"] != "identifier" or relation not in ISBN_RELATIONS:
                raise ValueError("invalid ISBN relation")
            if relation == "associated" and row.get("association_basis") != "provider_work_identity":
                raise ValueError("associated ISBN requires provider work identity")
            isbn[relation] += 1
    if sum(categories.values()) != denominator:
        raise ValueError("recoverability arithmetic failed")
    return {
        "schema": "recoverability-aggregate-v1",
        "contract": {"supported_regression": "closed-public-vocabulary", "exploratory": "edition-rights-only"},
        "supported": {"value_bearing_rows": denominator, "categories": dict(sorted(categories.items())), "by_field": {field: dict(sorted(counts.items())) for field, counts in sorted(by_field.items())}},
        "exploratory": {"categories": dict(sorted(exploratory.items()))},
        "excluded": dict(sorted(excluded.items())),
        "isbn": dict(sorted(isbn.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    aggregate = evaluate(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
