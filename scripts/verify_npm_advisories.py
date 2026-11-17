#!/usr/bin/env python3
"""Fail the frontend audit on every advisory the acceptance register does not hold.

`npm audit` has no way to accept one named advisory for a limited time: its
only knobs are a severity floor and dependency-type omissions, both of which
the acceptance register (`osv-scanner.toml`) exists to rule out. So the gate
runs `npm audit --json` itself and applies the same register the Python audit
uses. An advisory fails the gate unless an unexpired `[[IgnoredVulns]]` entry
names its GHSA identifier. Every other advisory, at every severity, still
fails, and an accepted one fails again on the day its `ignoreUntil` arrives.
`tests/unit/test_security_scanning_contract.py` holds each entry to a reason
and to an expiry at most 90 days out.

Run it from the `frontend` directory after `npm ci`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

ACCEPTANCE_REGISTER = Path(__file__).resolve().parents[1] / "osv-scanner.toml"
_GHSA = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}")


def accepted_advisories(register: Mapping[str, object], today: date) -> frozenset[str]:
    """The advisory identifiers the register still holds open on `today`."""

    entries = register.get("IgnoredVulns", [])
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return frozenset()
    accepted: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        identifier = entry.get("id")
        expiry = entry.get("ignoreUntil")
        if (
            isinstance(identifier, str)
            and isinstance(expiry, date)
            and not isinstance(expiry, datetime)
            and expiry > today
        ):
            accepted.add(identifier)
    return frozenset(accepted)


def reported_advisories(report: Mapping[str, object]) -> dict[str, str]:
    """Every advisory an `npm audit --json` report names, keyed by GHSA id.

    The value is the advisory's title. An advisory with no GHSA identifier is
    keyed by its URL, so it can never match a register entry.
    """

    vulnerabilities = report.get("vulnerabilities", {})
    if not isinstance(vulnerabilities, Mapping):
        raise ValueError("npm audit report has no vulnerabilities table")
    advisories: dict[str, str] = {}
    for finding in vulnerabilities.values():
        if not isinstance(finding, Mapping):
            raise ValueError("npm audit finding is not a table")
        via = finding.get("via", [])
        if not isinstance(via, Sequence) or isinstance(via, (str, bytes)):
            raise ValueError("npm audit finding has no via list")
        for source in via:
            # A string names another vulnerable package; its own entry carries
            # the advisory, so only advisory records are read here.
            if not isinstance(source, Mapping):
                continue
            url = str(source.get("url", ""))
            match = _GHSA.search(url)
            key = match[0] if match else url or repr(dict(source))
            advisories[key] = str(source.get("title", ""))
    return advisories


def unaccepted(
    report: Mapping[str, object], register: Mapping[str, object], today: date
) -> dict[str, str]:
    """The reported advisories the register does not hold open."""

    accepted = accepted_advisories(register, today)
    return {
        key: title
        for key, title in sorted(reported_advisories(report).items())
        if key not in accepted
    }


def main() -> int:
    completed = subprocess.run(
        ["npm", "audit", "--json"], capture_output=True, text=True, check=False
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(completed.stdout + completed.stderr)
        print("npm audit did not produce a JSON report", file=sys.stderr)
        return 1
    if not isinstance(report, Mapping) or "error" in report:
        sys.stderr.write(completed.stdout + completed.stderr)
        print("npm audit reported an error", file=sys.stderr)
        return 1
    register = tomllib.loads(ACCEPTANCE_REGISTER.read_text(encoding="utf-8"))
    today = date.today()
    failing = unaccepted(report, register, today)
    for key in sorted(set(reported_advisories(report)) - set(failing)):
        print(f"accepted until its register expiry: {key}")
    for key, title in failing.items():
        print(f"unaccepted advisory: {key} {title}", file=sys.stderr)
    if failing:
        return 1
    print("npm audit: no unaccepted advisories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
