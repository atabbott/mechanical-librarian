"""Count what #1481's sidecar rules would change in a retained snapshot.

A workbench instrument, not part of the installed product.  Run it from a
checkout against a COPY of the application database; it opens the file
read-only, writes nothing, and prints one aggregate JSON document:

```
uv run python scripts/audiobook_sidecar_replay.py --database /copy/application.sqlite3
```

The snapshot's reports predate per-document provenance, so they are read as
JSON rather than through the current report parser, and the counts are
"before" figures:

- ``audiobook_reports``: current reports with an audio section;
- ``entries_by_kind``: manifest entries by the kind this build derives from
  each name (`unrecognised` for a name it no longer reads);
- ``reports_with_sidecar_claims``: reports holding any `sidecar` claim;
- ``changed``: reports whose only sidecar is Audiobookshelf's own
  `metadata.json` or `metadata.abs`, which the new rules change wholly;
- ``undetermined``: reports with any other sidecar, which old reports cannot
  attribute to one document;
- ``unaffected``: reports with no sidecar.

Aggregate-only by construction: closed kinds and counts, never a name, title,
path or value.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    classify_sidecar,
)
from mechanical_librarian.domain.audio_inspection import AudioSidecarKind

REPLAY_SCHEMA = "audiobook-sidecar-replay-v1"
_REPORTS_SQL = (
    "SELECT mr.document FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "WHERE json_extract(mr.document,'$.audio') IS NOT NULL"
)


def _open_read_only(path: Path) -> sqlite3.Connection:
    absolute = str(path.absolute())
    return sqlite3.connect(f"file:{quote(absolute, safe='/')}?mode=ro", uri=True)


def _kind(name: str, media_names: tuple[str, ...]) -> str:
    classification = classify_sidecar(name, media_names)
    return "unrecognised" if classification is None else classification.kind.value


def replay(connection: sqlite3.Connection) -> dict[str, object]:
    """Read every current audiobook report inside one read transaction."""
    kinds: Counter[str] = Counter()
    reports = with_claims = changed = undetermined = unaffected = 0
    connection.execute("BEGIN")
    try:
        documents = [row[0] for row in connection.execute(_REPORTS_SQL)]
    finally:
        connection.execute("COMMIT")
    for document in documents:
        value = json.loads(document)
        audio = value.get("audio") or {}
        media_names = tuple(
            track["name"]
            for track in audio.get("tracks") or ()
            if type(track) is dict and type(track.get("name")) is str
        )
        names = [
            entry["name"]
            for entry in audio.get("sidecars") or ()
            if type(entry) is dict and type(entry.get("name")) is str
        ]
        derived = [_kind(name, media_names) for name in names]
        kinds.update(derived)
        reports += 1
        with_claims += any(
            type(claim) is dict and claim.get("basis") == "sidecar"
            for claim in value.get("local_claims") or ()
        )
        if not derived:
            unaffected += 1
        elif all(
            kind == AudioSidecarKind.AUDIOBOOKSHELF_RECORD.value for kind in derived
        ):
            changed += 1
        else:
            undetermined += 1
    return {
        "schema_version": REPLAY_SCHEMA,
        "audiobook_reports": reports,
        "entries_by_kind": dict(sorted(kinds.items())),
        "reports_with_sidecar_claims": with_claims,
        "changed": changed,
        "undetermined": undetermined,
        "unaffected": unaffected,
    }


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if not arguments.database.is_file():
        raise SystemExit("--database names no database")
    connection = _open_read_only(arguments.database)
    try:
        result = replay(connection)
    finally:
        connection.close()
    (stdout or sys.stdout).write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
