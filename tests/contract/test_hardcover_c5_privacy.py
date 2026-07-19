from __future__ import annotations

import copy
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

from mechanical_librarian.bootstrap.composition import (
    _C5_INTERNAL_OWNER,
    _run_hardcover_safe_executor_preview,
)
from mechanical_librarian.interfaces.cli import main
from tests.support.hardcover_c1_privacy import PrivacyEvidenceError
from tests.support.hardcover_c5_privacy import (
    C5_CAPTURE_CHANNELS,
    C5_FORBIDDEN_SOURCE_CLASSES,
    assert_c5_channels_private,
    c5_forbidden_corpus,
    c5_input_variants,
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
        raise PrivacyEvidenceError("C5 SQLite transient coverage is incomplete")


class HardcoverC5PrivacyContractTests(unittest.TestCase):
    def test_projection_oracle_covers_every_boundary_and_rejects_poison(self) -> None:
        variants = c5_input_variants(ROOT)
        with tempfile.TemporaryDirectory() as name:
            private_root = Path(name)
            private_root.chmod(0o700)
            variant_roots = tuple(
                private_root / f"variant-{index}" for index in range(len(variants))
            )
            for variant_root in variant_roots:
                variant_root.mkdir(mode=0o700)
            databases = tuple(
                variant_root / f"private-c5-state-{index}.sqlite"
                for index, variant_root in enumerate(variant_roots)
            )
            channels = {channel: [] for channel in C5_CAPTURE_CHANNELS}
            allowed = [
                (
                    ROOT
                    / "tests/fixtures/hardcover_identity/c5-safe-executor-example-output.json"
                ).read_text(encoding="utf-8"),
                "hardcover.synthetic.safe_executor",
                "safe-executor-v3",
                "one-attempt-case-set",
                "all_cases_passed",
                "hardcover_safe_executor_preview_total",
                "outcome",
                "1",
                "None",
                "Hardcover identity report failed safely.",
            ]
            corpus = c5_forbidden_corpus(
                ROOT, variants, databases, allowed_values=allowed
            )
            persistence_corpus = c5_forbidden_corpus(
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
            original_append = __import__(
                "mechanical_librarian.adapters.persistence.audit",
                fromlist=["SQLiteAuditRepository"],
            ).SQLiteAuditRepository.append
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

            def log_spy(logger, level, msg, args, *rest, **kwargs):
                channels["structured_logs"].append(
                    _render((logger.name, level, msg, args, rest, kwargs))
                )
                return original_log(logger, level, msg, args, *rest, **kwargs)

            def audit_spy(repository, record):
                channels["audit_logs"].append(_render(record))
                return original_append(repository, record)

            def hash_spy(algorithm):
                original = original_hashes[algorithm]

                def invoke(data=b"", *args, **kwargs):
                    channels["hash_inputs"].append(_render((algorithm, data)))
                    return original(data, *args, **kwargs)

                return invoke

            def fingerprint_spy(*args, **kwargs):
                channels["fingerprint_inputs"].append(_render((args, kwargs)))
                raise AssertionError("safe executor reached fingerprint authority")

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
                    return _run_hardcover_safe_executor_preview(
                        source, database, privacy_observer=observer
                    )
                except Exception as exc:
                    channels["exceptions"].append(
                        _render(
                            (
                                type(exc).__name__,
                                str(exc),
                                exc.args,
                                type(exc.__cause__).__name__
                                if exc.__cause__ is not None
                                else None,
                                str(exc.__cause__)
                                if exc.__cause__ is not None
                                else None,
                            )
                        )
                    )
                    raise
                finally:
                    active_database = None

            outputs = []
            report_type = __import__(
                "mechanical_librarian.application.hardcover_identity",
                fromlist=["SafeExecutorPreviewReport"],
            ).SafeExecutorPreviewReport
            original_to_json = report_type.to_json

            def export_spy(report):
                rendered = original_to_json(report)
                channels["exports"].append(rendered)
                channels["reports"].append(_render(report))
                return rendered

            patches = [
                patch.object(logging.Logger, "_log", new=log_spy),
                patch(
                    "mechanical_librarian.adapters.persistence.audit.SQLiteAuditRepository.append",
                    new=audit_spy,
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.Fingerprint",
                    new=fingerprint_spy,
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.connect_existing",
                    new=connect_existing_spy,
                ),
                patch.object(report_type, "to_json", new=export_spy),
            ]
            patches.extend(
                patch.object(hashlib, algorithm, new=hash_spy(algorithm))
                for algorithm in original_hashes
            )
            with ExitStack() as stack:
                for active_patch in patches:
                    stack.enter_context(active_patch)
                for variant, database in zip(variants, databases, strict=True):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    code = main(
                        [
                            "hardcover-identity-report",
                            "--mode",
                            "synthetic-preview",
                            "--scenario",
                            "safe-executor-v3",
                            "--input",
                            "-",
                            "--database",
                            os.fspath(database),
                            "--format",
                            "json",
                        ],
                        state_check_runner=lambda: None,
                        hardcover_safe_executor_preview_runner=runner,
                        stdin=io.StringIO(variant.payload.decode("utf-8")),
                        stdout=stdout,
                        stderr=stderr,
                    )
                    self.assertEqual(
                        code,
                        0,
                        channels["exceptions"][-1:] + [stderr.getvalue()],
                    )
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
            self.assertTrue(channels["traces"])
            self.assertTrue(channels["spans"])
            self.assertTrue(channels["metric_names"])
            self.assertTrue(channels["exports"])
            self.assertEqual(len(_C5_INTERNAL_OWNER), 55)
            for variant, database in zip(variants, databases, strict=True):
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
                self.assertTrue(owners.isdisjoint(variant.contributor_values))
                payload = database.read_bytes()
                channels["persistence"].extend(
                    (payload.decode("latin-1"), payload.hex())
                )

            invalid = copy.deepcopy(variants[0].source)
            invalid["cases"][0]["operation"] = "not-an-operation"
            invalid_root = private_root / "invalid"
            invalid_root.mkdir(mode=0o700)
            invalid_stdout = io.StringIO()
            invalid_stderr = io.StringIO()
            code = main(
                [
                    "hardcover-identity-report",
                    "--mode",
                    "synthetic-preview",
                    "--scenario",
                    "safe-executor-v3",
                    "--input",
                    "-",
                    "--database",
                    os.fspath(invalid_root / "invalid.sqlite"),
                    "--format",
                    "json",
                ],
                state_check_runner=lambda: None,
                hardcover_safe_executor_preview_runner=runner,
                stdin=io.StringIO(json.dumps(invalid)),
                stdout=invalid_stdout,
                stderr=invalid_stderr,
            )
            self.assertEqual(code, 2)
            channels["json_stdout"].append(invalid_stdout.getvalue())
            channels["stderr"].append(invalid_stderr.getvalue())

            text_stdout = io.StringIO()
            text_stderr = io.StringIO()
            text_root = private_root / "text"
            text_root.mkdir(mode=0o700)
            code = main(
                [
                    "hardcover-identity-report",
                    "--mode",
                    "synthetic-preview",
                    "--scenario",
                    "safe-executor-v3",
                    "--input",
                    "-",
                    "--database",
                    os.fspath(text_root / "text.sqlite"),
                    "--format",
                    "text",
                ],
                state_check_runner=lambda: None,
                hardcover_safe_executor_preview_runner=runner,
                stdin=io.StringIO(variants[0].payload.decode("utf-8")),
                stdout=text_stdout,
                stderr=text_stderr,
            )
            self.assertEqual(code, 2)
            channels["text_stdout"].append(text_stdout.getvalue())
            channels["stderr"].append(text_stderr.getvalue())

            try:
                assert_c5_channels_private(
                    channels, corpus, persistence_corpus=persistence_corpus
                )
            except PrivacyEvidenceError:
                collisions = [
                    (channel, source_class, projection)
                    for channel, values in channels.items()
                    for source_class, projections in corpus.items()
                    for projection in projections
                    if projection and projection in "\n".join(values)
                ]
                self.fail(f"unexpected privacy projection: {collisions[:3]!r}")
            with self.assertRaises(PrivacyEvidenceError):
                assert_c5_channels_private(
                    {key: value for key, value in channels.items() if key != "spans"},
                    corpus,
                    persistence_corpus=persistence_corpus,
                )
            with self.assertRaises(PrivacyEvidenceError):
                assert_c5_channels_private(
                    channels,
                    {
                        key: value
                        for key, value in corpus.items()
                        if key != "provider_identity"
                    },
                    persistence_corpus=persistence_corpus,
                )
            short_prefix = hashlib.blake2s(b"contributor-iota").hexdigest()[:8]
            full_digest = hashlib.blake2s(b"contributor-iota").hexdigest()
            self.assertIn(short_prefix, corpus["contributor_control"])
            self.assertNotIn(short_prefix, persistence_corpus["contributor_control"])
            collision = {key: list(values) for key, values in channels.items()}
            collision["persistence"].append(
                f"unrelated-work-key-bytes-{short_prefix}-only"
            )
            assert_c5_channels_private(
                collision, corpus, persistence_corpus=persistence_corpus
            )
            for poison in ("contributor-iota", full_digest):
                poisoned = {key: list(values) for key, values in channels.items()}
                poisoned["persistence"].append(poison)
                with self.assertRaises(PrivacyEvidenceError):
                    assert_c5_channels_private(
                        poisoned,
                        corpus,
                        persistence_corpus=persistence_corpus,
                    )
            probe_corpus = {
                source_class: frozenset({sorted(corpus[source_class])[0]})
                for source_class in C5_FORBIDDEN_SOURCE_CLASSES
            }
            persistence_probe_corpus = {
                source_class: frozenset(
                    {sorted(persistence_corpus[source_class])[0]}
                )
                for source_class in C5_FORBIDDEN_SOURCE_CLASSES
            }
            for channel in C5_CAPTURE_CHANNELS:
                for source_class in C5_FORBIDDEN_SOURCE_CLASSES:
                    active_corpus = (
                        persistence_probe_corpus
                        if channel == "persistence"
                        else probe_corpus
                    )
                    projection = next(iter(active_corpus[source_class]))
                    poisoned = {key: list(values) for key, values in channels.items()}
                    poisoned[channel].append(projection)
                    with self.assertRaises(PrivacyEvidenceError):
                        assert_c5_channels_private(
                            poisoned,
                            probe_corpus,
                            persistence_corpus=persistence_probe_corpus,
                        )


if __name__ == "__main__":
    unittest.main()
