"""A shared cross-thread connection never deadlocks on its Python SQL functions.

The installed runtime shares one ``sqlite3.Connection`` between the web thread
and the guarded-field-application worker, and registers ``ml_casefold`` and
``ml_cover_consideration_current`` on it. CPython runs a statement with the GIL
released and the SQLite connection mutex held, then retakes the GIL to call a
Python SQL function; binding a parameter takes the same mutex with the GIL
held. Two threads using that connection at once can therefore each hold what
the other needs, permanently (#1020). The only defence is that no two threads
ever use the connection at once: every statement runs under the connection's
serialization lock (ADR-0077).

A hang freezes the whole interpreter, so the scenario runs in a child process
and a deadline turns a deadlock into a failure rather than a stuck test run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_report import MetadataReport, TargetFacts
from tests.support.review_cases import _report

_WORKS = 200

_CHILD = """
import json, sys, threading
from pathlib import Path
from mechanical_librarian.adapters.persistence import metadata_reports, schema
from mechanical_librarian.application.operation_admission import OperationAdmission
from mechanical_librarian.application.review_web import BrowseReviewLibrary

casefold = schema._review_casefold
calls = [0]
def counted(value):
    calls[0] += 1
    return casefold(value)
schema._review_casefold = counted

repository = metadata_reports.open_repository(
    Path(sys.argv[1]), create=False, cross_thread=True, admission=OperationAdmission()
)
# Open-time fact validation folds every report; count only the reads below.
calls[0] = 0
unserialized = []
def audit(sql):
    if not repository.operation_lock._is_owned():
        unserialized.append((threading.current_thread().name, sql[:80]))
repository._connection.set_trace_callback(audit)

browse = BrowseReviewLibrary(repository)
failures = []
def read():
    try:
        for _ in range(30):
            browse.library(lens="all", search="work 1", limit=20, cursor=None)
            browse.library(lens="needs_attention", search="work", limit=20, cursor=None)
    except BaseException as error:
        failures.append(repr(error))
def write():
    try:
        for index in range(1, 301):
            repository.suspend_field_effect_capability(
                scope="destination", connection="primary", destination="title",
                reason="field_unavailable", changed_at=index,
            )
            repository.clear_field_effect_capability_suspension(
                scope="destination", connection="primary", destination="title",
                reason="field_unavailable", observed_at=index,
            )
    except BaseException as error:
        failures.append(repr(error))
threads = [
    threading.Thread(target=read, name="review-read"),
    threading.Thread(target=write, name="guarded-field-application"),
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
repository.close()
print(json.dumps({"casefold_calls": calls[0], "failures": failures,
                  "unserialized": unserialized}))
"""


def test_review_reads_and_guarded_writes_share_one_connection_without_deadlock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    for index in range(_WORKS):
        source = _report(title=f"Work {index:03d}")
        repository.publish(
            MetadataReport.create(
                target=TargetFacts(
                    "primary", "ebooks", f"work-{index:03d}", source.target.fields
                ),
                file=source.file,
                local_claims=source.local_claims,
                local_reasons=source.local_reasons,
                providers=source.providers,
            ),
            lambda: True,
        )
    repository.close()

    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD, str(database)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "concurrent review reads and guarded writes deadlocked the shared "
            "connection"
        ) from None
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["failures"] == []
    assert outcome["unserialized"] == []
    # The read path really evaluated a Python SQL function while the writer ran.
    assert outcome["casefold_calls"] > _WORKS
