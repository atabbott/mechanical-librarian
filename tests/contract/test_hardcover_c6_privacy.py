from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.application.hardcover_identity import (
    IdentityLifecyclePreviewReport,
)
from mechanical_librarian.bootstrap.composition import (
    _C5_INTERNAL_OWNER,
    _run_hardcover_identity_lifecycle_preview,
)
from mechanical_librarian.interfaces.cli import main
from tests.support.hardcover_c1_privacy import PrivacyEvidenceError
from tests.support.hardcover_c6_privacy import (
    C6_CAPTURE_CHANNELS,
    C6_FORBIDDEN_SOURCE_CLASSES,
    assert_c6_channels_private,
    c6_forbidden_corpus,
    c6_input_variants,
    resource_digests,
)


ROOT = Path(__file__).parents[2]


def _render(value: object) -> str:
    try:
        return json.dumps(value, default=repr, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError, RecursionError):
        return repr(value)


def _assert_transient_coverage(
    coverage: dict[Path, set[str]], databases: tuple[Path, ...]
) -> None:
    if set(coverage) != set(databases) or any(
        coverage[database] != {"main", "wal", "shm"} for database in databases
    ):
        raise PrivacyEvidenceError("C6 SQLite transient coverage is incomplete")


class HardcoverC6PrivacyTests(unittest.TestCase):
    def test_every_forbidden_source_is_absent_from_every_c1_channel(self) -> None:
        variants = c6_input_variants(ROOT)
        with tempfile.TemporaryDirectory() as name:
            private_root = Path(name)
            private_root.chmod(0o700)
            roots = tuple(
                private_root / f"variant-{index}" for index in range(len(variants))
            )
            for root in roots:
                root.mkdir(mode=0o700)
            databases = tuple(
                root / f"private-c6-state-{index}.sqlite"
                for index, root in enumerate(roots)
            )
            channels = {channel: [] for channel in C6_CAPTURE_CHANNELS}
            allowed = [
                (
                    ROOT
                    / "tests/fixtures/hardcover_identity/"
                    "c6-lifecycle-orchestration-example-output.json"
                ).read_text("utf-8"),
                "hardcover.synthetic.identity_lifecycle",
                "lifecycle-orchestration-v3",
                "foreground-lifecycle-case-set",
                "all_cases_passed",
                "hardcover_identity_lifecycle_preview_total",
                "outcome",
                "1",
                "None",
                "Hardcover identity report failed safely.",
                *(str(path) for path in databases),
                *(path.name for path in databases),
                *(str(path.parent) for path in databases),
                *(path.parent.name for path in databases),
            ]
            corpus = c6_forbidden_corpus(
                ROOT, variants, databases, allowed_values=allowed
            )
            persistence_corpus = c6_forbidden_corpus(
                ROOT,
                variants,
                databases,
                allowed_values=allowed,
                persistence_safe=True,
            )
            transient_coverage: dict[Path, set[str]] = {}
            active_database: Path | None = None
            checkpoint_connection: sqlite3.Connection | None = None
            original_log = logging.Logger._log
            original_connect_existing = __import__(
                "mechanical_librarian.bootstrap.composition",
                fromlist=["connect_existing"],
            ).connect_existing
            original_hashes = {
                algorithm: getattr(hashlib, algorithm)
                for algorithm in (
                    "md5",
                    "sha1",
                    "sha224",
                    "sha256",
                    "sha384",
                    "sha512",
                    "blake2b",
                    "blake2s",
                )
            }
            original_to_json = IdentityLifecyclePreviewReport.to_json

            def log_spy(logger, level, msg, args, *rest, **kwargs):
                channels["structured_logs"].append(
                    _render((logger.name, level, msg, args, rest, kwargs))
                )
                return original_log(logger, level, msg, args, *rest, **kwargs)

            def hash_spy(algorithm: str):
                original = original_hashes[algorithm]

                def invoke(data=b"", *args, **kwargs):
                    channels["hash_inputs"].append(_render((algorithm, data)))
                    return original(data, *args, **kwargs)

                return invoke

            def fingerprint_spy(*args, **kwargs):
                channels["fingerprint_inputs"].append(_render((args, kwargs)))
                raise AssertionError("lifecycle preview reached fingerprint authority")

            def connect_existing_spy(path):
                nonlocal checkpoint_connection
                connection = original_connect_existing(path)
                if path == active_database:
                    checkpoint_connection = connection
                return connection

            def observer(channel: str, values: tuple[object, ...]) -> None:
                if channel == "persistence_checkpoint":
                    self.assertEqual(values, ("sqlite-main-wal-shm",))
                    self.assertIsNotNone(active_database)
                    self.assertIsNotNone(checkpoint_connection)
                    assert active_database is not None
                    assert checkpoint_connection is not None
                    self.assertEqual(
                        checkpoint_connection.execute(
                            "PRAGMA journal_mode = WAL"
                        ).fetchone(),
                        ("wal",),
                    )
                    original_updated_at = checkpoint_connection.execute(
                        "SELECT updated_at FROM hardcover_identity_generations "
                        "WHERE rowid=(SELECT MIN(rowid) "
                        "FROM hardcover_identity_generations)"
                    ).fetchone()
                    self.assertIsNotNone(original_updated_at)
                    checkpoint_connection.execute("BEGIN IMMEDIATE")
                    checkpoint_connection.execute(
                        "UPDATE hardcover_identity_generations "
                        "SET updated_at='2099-01-01T00:00:00.000000Z' "
                        "WHERE rowid=(SELECT MIN(rowid) "
                        "FROM hardcover_identity_generations)"
                    )
                    checkpoint_connection.commit()
                    paths = {
                        "main": active_database,
                        "wal": Path(f"{active_database}-wal"),
                        "shm": Path(f"{active_database}-shm"),
                    }
                    try:
                        presence = {
                            kind: path.is_file() for kind, path in paths.items()
                        }
                        self.assertTrue(all(presence.values()), presence)
                        transient_coverage[active_database] = set(paths)
                        for payload in (
                            path.read_bytes() for path in paths.values()
                        ):
                            self.assertTrue(payload)
                            channels["persistence"].extend(
                                (payload.decode("latin-1"), payload.hex())
                            )
                    finally:
                        checkpoint_connection.execute("BEGIN IMMEDIATE")
                        checkpoint_connection.execute(
                            "UPDATE hardcover_identity_generations "
                            "SET updated_at=? WHERE rowid=(SELECT MIN(rowid) "
                            "FROM hardcover_identity_generations)",
                            original_updated_at,
                        )
                        checkpoint_connection.commit()
                        self.assertEqual(
                            checkpoint_connection.execute(
                                "SELECT updated_at "
                                "FROM hardcover_identity_generations "
                                "WHERE rowid=(SELECT MIN(rowid) "
                                "FROM hardcover_identity_generations)"
                            ).fetchone(),
                            original_updated_at,
                        )
                elif channel == "metrics":
                    name, labels, value, exemplar = values
                    channels["metric_names"].append(_render(name))
                    channels["metric_labels"].append(_render(labels))
                    channels["metric_values"].append(_render(value))
                    channels["metric_exemplars"].append(_render(exemplar))
                else:
                    channels[channel].append(_render(values))

            def runner(source, database):
                nonlocal active_database
                active_database = database
                try:
                    report = _run_hardcover_identity_lifecycle_preview(
                        source, database, privacy_observer=observer
                    )
                    channels["runtime_evidence"].append(_render(report))
                    return report
                except Exception as failure:
                    channels["exceptions"].append(
                        _render(
                            (
                                type(failure).__name__,
                                str(failure),
                                failure.args,
                                type(failure.__cause__).__name__
                                if failure.__cause__ is not None
                                else None,
                                str(failure.__cause__)
                                if failure.__cause__ is not None
                                else None,
                                getattr(failure, "__notes__", None),
                            )
                        )
                    )
                    raise
                finally:
                    active_database = None

            def export_spy(report):
                rendered = original_to_json(report)
                channels["exports"].append(rendered)
                channels["reports"].append(_render(report))
                return rendered

            patches = [
                patch.object(logging.Logger, "_log", new=log_spy),
                patch(
                    "mechanical_librarian.bootstrap.composition.Fingerprint",
                    new=fingerprint_spy,
                ),
                patch.object(
                    IdentityLifecyclePreviewReport, "to_json", new=export_spy
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.connect_existing",
                    new=connect_existing_spy,
                ),
            ]
            patches.extend(
                patch.object(hashlib, algorithm, new=hash_spy(algorithm))
                for algorithm in original_hashes
            )
            outputs = []
            with ExitStack() as stack:
                for active in patches:
                    stack.enter_context(active)
                for variant, database in zip(variants, databases, strict=True):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    code = main(
                        [
                            "hardcover-identity-report",
                            "--mode",
                            "synthetic-preview",
                            "--scenario",
                            "lifecycle-orchestration-v3",
                            "--input",
                            "-",
                            "--database",
                            os.fspath(database),
                            "--format",
                            "json",
                        ],
                        state_check_runner=lambda: None,
                        hardcover_identity_lifecycle_preview_runner=runner,
                        stdin=io.StringIO(variant.payload.decode("utf-8")),
                        stdout=stdout,
                        stderr=stderr,
                    )
                    self.assertEqual(code, 0, channels["exceptions"][-1:])
                    channels["json_stdout"].append(stdout.getvalue())
                    channels["stderr"].append(stderr.getvalue())
                    outputs.append(stdout.getvalue())

            self.assertEqual(len(set(outputs)), 1)
            _assert_transient_coverage(transient_coverage, databases)
            with self.assertRaises(PrivacyEvidenceError):
                _assert_transient_coverage(
                    {
                        database: ({"main", "wal"} if index == 0 else set(kinds))
                        for index, (database, kinds) in enumerate(
                            transient_coverage.items()
                        )
                    },
                    databases,
                )
            for database in databases:
                connection = sqlite3.connect(database)
                try:
                    owners = {
                        row[0]
                        for row in connection.execute(
                            "SELECT DISTINCT owner FROM hardcover_identity_claims"
                        )
                    }
                finally:
                    connection.close()
                self.assertEqual(owners, {_C5_INTERNAL_OWNER})
                self.assertTrue(
                    all(
                        owners.isdisjoint(variant.contributor_values)
                        for variant in variants
                    )
                )
                for path in database.parent.iterdir():
                    channels["file_names"].append(path.name)
                    channels["file_paths"].append(str(path))
                    if path.is_file():
                        payload = path.read_bytes()
                        channels["persistence"].extend(
                            (payload.decode("latin-1"), payload.hex())
                        )
            channels["text_stdout"].append("")
            channels["audit_logs"].append("")

            try:
                assert_c6_channels_private(
                    channels,
                    corpus,
                    persistence_corpus=persistence_corpus,
                )
            except PrivacyEvidenceError as failure:
                self.fail(str(failure))
            self.assertEqual(len(resource_digests(ROOT)), 8)
            self.assertTrue(all(corpus[source] for source in C6_FORBIDDEN_SOURCE_CLASSES))

            representatives = {
                source: next(iter(corpus[source]))
                for source in C6_FORBIDDEN_SOURCE_CLASSES
            }
            persistence_representatives = {
                source: next(iter(persistence_corpus[source]))
                for source in C6_FORBIDDEN_SOURCE_CLASSES
            }
            poison_corpus = {
                source: frozenset({representative})
                for source, representative in representatives.items()
            }
            for channel in C6_CAPTURE_CHANNELS:
                for source in C6_FORBIDDEN_SOURCE_CLASSES:
                    if channel == "persistence" and source == "provider_identity":
                        continue
                    active_representatives = (
                        persistence_representatives
                        if channel == "persistence"
                        else representatives
                    )
                    poisoned = {key: list(values) for key, values in channels.items()}
                    poisoned[channel].append(active_representatives[source])
                    with self.assertRaises(PrivacyEvidenceError):
                        assert_c6_channels_private(
                            poisoned,
                            poison_corpus,
                            persistence_corpus={
                                key: frozenset({value})
                                for key, value in persistence_representatives.items()
                            },
                        )


if __name__ == "__main__":
    unittest.main()
