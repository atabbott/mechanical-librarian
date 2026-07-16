#!/usr/bin/env python3
"""Qualify an installed F07b CLI with aggregate-only disposable evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone


_SELECTOR = re.compile(
    r"^target=(target_[0-9a-f]{32}) snapshot=(snapshot_[0-9a-f]{32}) "
    r"file-index=([0-9]+) media=epub$"
)
_SUCCESS = re.compile(
    r"^mode=(executed|reused) evidence=(evidence_[0-9a-f]{32}) "
    r"current=(true|false) outcome=([a-z0-9_]+)$"
)
_FAILURE = re.compile(r"^failure=([a-z0-9_]+)$")
_LIBRARY_ID = "00000000-0000-4000-8000-000000000009"


class QualificationError(ValueError):
    pass


def _configuration(cohort: Path) -> dict[str, object]:
    return {
        "schema_version": "config-v1",
        "instance_name": "epub-evidence-qualification",
        "connections": [
            {
                "adapter": "audiobookshelf",
                "id": "qualification-connection",
                "endpoint": "https://qualification.invalid",
                "secret_reference": "unused-qualification-secret",
                "enabled_libraries": [_LIBRARY_ID],
            }
        ],
        "media_mounts": [
            {
                "name": "cohort",
                "library_type": "ebook",
                "container_path": str(cohort),
                "service_path": "/qualification",
                "read_only": True,
            }
        ],
        "evidence": {"maximum_document_bytes": 268_435_456},
        "providers": [],
        "reviewers": [],
        "notifications": [],
        "backups_enabled": True,
    }


def _run(command: tuple[str, ...], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _evidence(
    executable: Path,
    config: Path,
    database: Path,
    selector: dict[str, object],
) -> tuple[bool, dict[str, str]]:
    completed = _run(
        (
            str(executable),
            "epub-opening-evidence",
            "--database",
            str(database),
            "--config",
            str(config),
            "--target",
            str(selector["target"]),
            "--snapshot",
            str(selector["snapshot"]),
            "--file-index",
            str(selector["file_index"]),
        )
    )
    lines = (completed.stdout if completed.returncode == 0 else completed.stderr).splitlines()
    if completed.returncode == 0:
        matches = [_SUCCESS.fullmatch(line) for line in lines]
        match = next((value for value in matches if value is not None), None)
        if match is None:
            raise QualificationError("installed evidence success envelope is malformed")
        return True, {
            "mode": match.group(1),
            "evidence": match.group(2),
            "current": match.group(3),
            "outcome": match.group(4),
        }
    match = next(
        (value for line in lines if (value := _FAILURE.fullmatch(line)) is not None),
        None,
    )
    if match is None:
        raise QualificationError("installed evidence failure envelope is malformed")
    return False, {"failure": match.group(1)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--cohort", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--head-commit", default="unbound")
    parser.add_argument("--head-tree", default="unbound")
    parser.add_argument("--artifact-sha256", default="unbound")
    parser.add_argument("--environment", default="unbound")
    arguments = parser.parse_args()
    if not arguments.executable.is_file() or not arguments.python.is_file():
        return 2
    if (
        (arguments.head_commit != "unbound" and re.fullmatch(r"[0-9a-f]{40}", arguments.head_commit) is None)
        or (arguments.head_tree != "unbound" and re.fullmatch(r"[0-9a-f]{40}", arguments.head_tree) is None)
        or (arguments.artifact_sha256 != "unbound" and re.fullmatch(r"[0-9a-f]{64}", arguments.artifact_sha256) is None)
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", arguments.environment) is None
    ):
        return 2
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shadow = root / "cohort"
        shadow.mkdir()
        allowlisted: set[str] | None = None
        if arguments.manifest is not None:
            manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
            allowlisted = {
                str(value["materialized_filename"])
                for value in manifest.get("fixtures", [])
                if type(value) is dict
                and type(value.get("consumer_ownership")) is list
                and "f07a-opening-qualification" in value["consumer_ownership"]
                and type(value.get("materialized_filename")) is str
            }
        for source in sorted(arguments.cohort.rglob("*")):
            if (
                source.is_symlink()
                or not source.is_file()
                or source.suffix.casefold() != ".epub"
                or (
                    allowlisted is not None
                    and source.relative_to(arguments.cohort).as_posix()
                    not in allowlisted
                )
            ):
                continue
            relative = source.relative_to(arguments.cohort)
            destination = shadow / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
        config = root / "configuration.json"
        database = root / "state.sqlite3"
        selector_map_path = root / "selector-map.json"
        config.write_text(
            json.dumps(_configuration(shadow)), encoding="utf-8"
        )
        prepared = _run(
            (
                str(arguments.python),
                str(Path(__file__).with_name("prepare_epub_evidence_state.py")),
                "--config",
                str(config),
                "--database",
                str(database),
                "--selector-map",
                str(selector_map_path),
            )
        )
        if prepared.returncode != 0:
            raise QualificationError("disposable evidence state could not be prepared")
        selector_map = json.loads(selector_map_path.read_text(encoding="utf-8"))
        inspected = _run(
            (
                str(arguments.executable),
                "inventory-inspect",
                "--config",
                str(config),
                "--database",
                str(database),
            )
        )
        if inspected.returncode != 0:
            raise QualificationError("installed selector inspection failed")
        observed = {
            (match.group(1), match.group(2), int(match.group(3)))
            for line in inspected.stdout.splitlines()
            if (match := _SELECTOR.fullmatch(line)) is not None
        }
        expected = {
            (str(value["target"]), str(value["snapshot"]), int(value["file_index"]))
            for value in selector_map
        }
        if observed != expected or not observed:
            raise QualificationError("installed selector cohort is incomplete")

        initial: dict[tuple[str, str, int], dict[str, str]] = {}
        failures = Counter()
        outcomes = Counter()
        for selector in selector_map:
            key = (
                str(selector["target"]),
                str(selector["snapshot"]),
                int(selector["file_index"]),
            )
            succeeded, value = _evidence(
                arguments.executable, config, database, selector
            )
            if succeeded:
                if value["mode"] != "executed" or value["current"] != "true":
                    raise QualificationError("first evidence execution was not current")
                initial[key] = value
                outcomes[value["outcome"]] += 1
            else:
                failures[value["failure"]] += 1
        if not initial:
            raise QualificationError("cohort produced no durable evidence")

        reused = 0
        for selector in selector_map:
            key = (
                str(selector["target"]),
                str(selector["snapshot"]),
                int(selector["file_index"]),
            )
            if key not in initial:
                continue
            succeeded, value = _evidence(
                arguments.executable, config, database, selector
            )
            if (
                not succeeded
                or value["mode"] != "reused"
                or value["evidence"] != initial[key]["evidence"]
                or value["current"] != "true"
            ):
                raise QualificationError("restart reuse was not identity-stable")
            reused += 1

        changed_selector = next(
            value
            for value in selector_map
            if (
                str(value["target"]),
                str(value["snapshot"]),
                int(value["file_index"]),
            )
            in initial
        )
        changed_path = shadow / str(changed_selector["relative_path"])
        original = changed_path.read_bytes()
        try:
            changed_path.write_bytes(original + b"\x00")
            succeeded, changed = _evidence(
                arguments.executable, config, database, changed_selector
            )
            key = (
                str(changed_selector["target"]),
                str(changed_selector["snapshot"]),
                int(changed_selector["file_index"]),
            )
            if (
                not succeeded
                or changed["mode"] != "executed"
                or changed["evidence"] == initial[key]["evidence"]
            ):
                raise QualificationError("controlled source invalidation did not isolate")
        finally:
            changed_path.write_bytes(original)
        succeeded, reverse = _evidence(
            arguments.executable, config, database, changed_selector
        )
        if (
            not succeeded
            or reverse["mode"] != "reused"
            or reverse["evidence"] != initial[key]["evidence"]
            or reverse["current"] != "false"
        ):
            raise QualificationError("reverse replay rewrote current applicability")

        isolated = next(
            (value for value in selector_map if value is not changed_selector),
            None,
        )
        failure_isolated = 0
        if isolated is not None:
            isolated_path = shadow / str(isolated["relative_path"])
            mode = stat.S_IMODE(isolated_path.stat().st_mode)
            try:
                os.chmod(isolated_path, 0)
                succeeded, _ = _evidence(
                    arguments.executable, config, database, isolated
                )
                if succeeded:
                    raise QualificationError("unavailable source did not fail closed")
                continuation = next(
                    value
                    for value in selector_map
                    if value is not isolated and value is not changed_selector
                )
                continued, continued_value = _evidence(
                    arguments.executable, config, database, continuation
                )
                continued_key = (
                    str(continuation["target"]),
                    str(continuation["snapshot"]),
                    int(continuation["file_index"]),
                )
                if (
                    not continued
                    or continued_value["mode"] != "reused"
                    or continued_value["evidence"] != initial[continued_key]["evidence"]
                    or continued_value["current"] != "true"
                ):
                    raise QualificationError("unrelated evidence did not continue")
                failure_isolated = 1
            finally:
                os.chmod(isolated_path, mode)

        selector_map_path.unlink(missing_ok=True)
        database.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "aggregate": {
                        "durable": len(initial),
                        "failure_isolation": failure_isolated,
                        "operational_failures": dict(sorted(failures.items())),
                        "outcomes": dict(sorted(outcomes.items())),
                        "restart_reused": reused,
                        "selectors": len(observed),
                        "source_invalidations": 1,
                    },
                    "artifact": "installed-wheel-cli",
                    "artifact_sha256": arguments.artifact_sha256,
                    "environment": arguments.environment,
                    "head_commit": arguments.head_commit,
                    "head_tree": arguments.head_tree,
                    "observed_at": observed_at,
                    "privacy": "aggregate-only-disposable-state-deleted",
                    "schema": "epub-opening-evidence-qualification-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as error:
        raise SystemExit(str(error)) from None
