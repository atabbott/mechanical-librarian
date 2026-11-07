"""The series replay behind the admission-rule ledger's W9, A8 and A9 rows (#1748).

W9 (the series identity match, #998), A8 (series membership completeness,
#817) and A9 (series provider evidence, #991) refuse a series value the
proposal set already proposed, and their stored reasons exist only for fields
the policy sends to Automatic. So the ledger replays the product's own series
path under each snapshot's own source.

A child ``python -I -B`` process puts only that source tree on ``sys.path``
and refuses (exit 3) when any loaded ``mechanical_librarian.*`` or
``scripts.*`` module resolves outside it. It opens the database read-only
(``mode=ro&immutable=1``, ``query_only``) and runs the parent's case query, so
an ordinal means the same case in both processes. For each case with a series
result it decodes the report and the proposal set with the source's own
``from_json``, reads the field's stored series option from the latest shadow
outcome (the part before any ``:``), and calls the source's
``_project("series", option, "automatic", ...)``: the field's stored setting,
as if the field were sent to Automatic.

The source's ``_automatic_series_abstention`` is wrapped in the
``rich_metadata_preview`` namespace to see its ``candidate`` argument. A field
is reached when that call ran with a candidate; only a reached field is
attributed, by the reason ``_project`` returns:
``target_work_applicability_unproved`` to W9, ``series_membership_incomplete``
to A8, and ``series_provider_evidence_required`` or
``series_provider_failure_*`` to A9. A W9 field is actionable when the same
call, with ``provider_candidate_represents_target_series_work`` patched to
accept in that namespace, projects the value.

The child prints only counts and ordinals. The parent validates that shape,
never prints, logs or embeds the child's stderr, and refuses any failure with
the fixed line "series replay failed".
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

# Direct execution puts scripts/ on sys.path and the ledger aliases itself
# there before importing this module; the test import resolves the scripts
# namespace package.
try:
    from scripts import admission_rule_ledger as ledger
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import admission_rule_ledger as ledger  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

_FAILED = "series replay failed"
COUNTS = (
    "replayed",
    "unreplayed",
    "reached",
    "unreached_ask",
    "unreached_earlier",
    "unreached_no_candidate",
)
_KEYS = frozenset({*COUNTS, "W9", "A8", "A9"})

_SERIES_CHILD = r"""
import inspect, json, os, sqlite3, sys
from urllib.parse import quote
root = os.path.realpath(sys.argv[1])
sys.path[:0] = [os.path.join(root, "src"), root]


def scoped():
    # Only product and script modules are scoped; the standard library and
    # the interpreter's own packages load from outside the root.
    for name, module in list(sys.modules.items()):
        if name.partition(".")[0] not in ("mechanical_librarian", "scripts"):
            continue
        paths = [getattr(module, "__file__", None)]
        paths += list(getattr(module, "__path__", []) or [])
        for path in paths:
            if path is None:
                continue
            if not os.path.realpath(path).startswith(root + os.sep):
                raise SystemExit(3)


from mechanical_librarian.domain import rich_metadata_preview as R
from mechanical_librarian.domain.metadata_report import (
    MetadataReport, TargetDestination)
from mechanical_librarian.domain.proposal_set import ProposalSet
scoped()
PARAMETERS = ("field", "option", "destination", "target", "result", "proposal",
              "report", "retained_claims")
names = ("_project", "_retained_claims", "_automatic_series_abstention",
         "provider_candidate_represents_target_series_work",
         "admitted_audiobook_identity")
if any(not callable(getattr(R, name, None)) for name in names):
    raise SystemExit(4)
project, retained, abstention = R._project, R._retained_claims, R._automatic_series_abstention
predicate, admitted = (R.provider_candidate_represents_target_series_work,
                       R.admitted_audiobook_identity)
if tuple(inspect.signature(project).parameters) != PARAMETERS:
    raise SystemExit(4)
seen = {"ran": False, "candidate": False}


def observed(report, result, candidate, retained_claims):
    seen["ran"] = True
    seen["candidate"] = candidate is not None
    return abstention(report, result, candidate, retained_claims)


def accept(*_arguments, **_keywords):
    return True


R._automatic_series_abstention = observed
path = quote(os.path.abspath(sys.argv[2]), safe="/")
connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
connection.execute("PRAGMA query_only=ON")
ONE = ("SELECT rc.document,ps.document,mr.document,"
       "(SELECT shadow.document FROM shadow_decision_outcomes shadow "
       "WHERE shadow.review_case_id=rc.review_case_id "
       "ORDER BY shadow.evaluated_at DESC LIMIT 1) "
       "FROM review_cases rc JOIN proposal_sets ps "
       "ON ps.proposal_set_id=rc.proposal_set_id "
       "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
       "WHERE rc.review_case_id=?")
counts = dict.fromkeys(("replayed", "unreplayed", "reached", "unreached_ask",
                        "unreached_earlier", "unreached_no_candidate"), 0)
flagged = {"W9": [], "A8": [], "A9": []}
cases = [(row[0], bool(row[1])) for row in connection.execute(sys.argv[3])]
for ordinal, (case_id, current) in enumerate(cases):
    row = connection.execute(ONE, (case_id,)).fetchone()
    if row is None:
        continue
    case_text, proposal_text, report_text, shadow_text = row
    report = MetadataReport.from_json(report_text)
    proposal = ProposalSet.from_json(proposal_text)
    results = [value for value in proposal.results
               if value.destination is TargetDestination.SERIES]
    if not results:
        continue
    shadow = json.loads(shadow_text) if isinstance(shadow_text, str) else {}
    options = {
        outcome["policy_option"].partition(":")[0]
        for outcome in (shadow.get("outcomes") or [])
        if isinstance(outcome, dict) and outcome.get("destination") == "series"
        and isinstance(outcome.get("policy_option"), str)
    }
    if len(options) != 1:
        counts["unreplayed"] += current
        continue
    option = options.pop()
    target = {value.destination: value for value in report.target.fields}[
        TargetDestination.SERIES]
    arguments = ("series", option, "automatic", target, results[0], proposal,
                 report, retained(report))
    seen.update(ran=False, candidate=False)
    preview = project(*arguments)
    reached = seen["ran"] and seen["candidate"]
    if current:
        counts["replayed"] += 1
        if reached:
            counts["reached"] += 1
        elif seen["ran"]:
            counts["unreached_no_candidate"] += 1
        elif report.audio is not None and not admitted(report):
            counts["unreached_ask"] += 1
        else:
            counts["unreached_earlier"] += 1
    if not reached or preview.outcome != "abstain":
        continue
    reason = preview.reason
    if reason == "target_work_applicability_unproved":
        R.provider_candidate_represents_target_series_work = accept
        try:
            patched = project(*arguments)
        finally:
            R.provider_candidate_represents_target_series_work = predicate
        flagged["W9"].append([ordinal, patched.outcome == "projected"])
    elif reason == "series_membership_incomplete":
        flagged["A8"].append(ordinal)
    elif isinstance(reason, str) and (
        reason == "series_provider_evidence_required"
        or reason.startswith("series_provider_failure_")
    ):
        flagged["A9"].append(ordinal)
scoped()
sys.stdout.write(json.dumps({**counts, **flagged}))
"""


@dataclass(frozen=True, slots=True)
class SeriesProbe:
    """What the series replay found, as counts of current cases and ordinals."""

    counts: Mapping[str, int]
    # ordinal -> whether the value projects with the identity match accepting
    w9: Mapping[int, bool]
    a8: frozenset[int]
    a9: frozenset[int]

    def flags(self, ordinal: int) -> dict[str, bool]:
        """The replay rows that refuse this case's series field, with W9's actionability."""
        found: dict[str, bool] = {}
        if ordinal in self.w9:
            found["W9"] = self.w9[ordinal]
        if ordinal in self.a8:
            found["A8"] = False
        if ordinal in self.a9:
            found["A9"] = False
        return found


SeriesReader = Callable[[Path, Path, str], SeriesProbe]


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError
    return value


def parse_series_probe(text: str) -> SeriesProbe:
    """Validate the child's document: only counts and ordinals pass."""
    try:
        loaded = cast("dict[str, object]", json.loads(text))
        if not isinstance(loaded, dict) or set(loaded) != _KEYS:
            raise ValueError
        counts = {key: _count(loaded[key]) for key in COUNTS}
        w9: dict[int, bool] = {}
        for entry in cast("list[object]", loaded["W9"]):
            ordinal, actionable = cast("list[object]", entry)
            if not isinstance(actionable, bool):
                raise ValueError
            w9[_count(ordinal)] = actionable
        a8 = frozenset(_count(item) for item in cast("list[object]", loaded["A8"]))
        a9 = frozenset(_count(item) for item in cast("list[object]", loaded["A9"]))
    except (TypeError, ValueError, AttributeError):
        raise ledger.Refusal(_FAILED) from None
    return SeriesProbe(counts, w9, a8, a9)


def child_series(source_root: Path, database: Path, cases_sql: str) -> SeriesProbe:
    """Replay every listed case's series field under its source in a child."""
    if not (source_root / "src" / "mechanical_librarian").is_dir():
        raise ledger.Refusal("source root is not an exported source tree")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _SERIES_CHILD,
                str(source_root),
                str(database),
                cases_sql,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=7200,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ledger.Refusal(_FAILED) from None
    if completed.returncode != 0:
        raise ledger.Refusal(_FAILED)
    return parse_series_probe(completed.stdout)
