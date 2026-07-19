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
            original_log = logging.Logger._log
            original_append = __import__(
                "mechanical_librarian.adapters.persistence.audit",
                fromlist=["SQLiteAuditRepository"],
            ).SQLiteAuditRepository.append
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

            def observer(channel: str, values: tuple[object, ...]) -> None:
                if channel == "metrics":
                    name, labels, value, exemplar = values
                    channels["metric_names"].append(_render(name))
                    channels["metric_labels"].append(_render(labels))
                    channels["metric_values"].append(_render(value))
                    channels["metric_exemplars"].append(_render(exemplar))
                else:
                    channels[channel].append(_render(values))

            def runner(source, database):
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
                for path in database.parent.iterdir():
                    if path.name.startswith(database.name):
                        payload = path.read_bytes()
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
                assert_c5_channels_private(channels, corpus)
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
                )
            with self.assertRaises(PrivacyEvidenceError):
                assert_c5_channels_private(
                    channels,
                    {
                        key: value
                        for key, value in corpus.items()
                        if key != "provider_identity"
                    },
                )
            probe_corpus = {
                source_class: frozenset({sorted(corpus[source_class])[0]})
                for source_class in C5_FORBIDDEN_SOURCE_CLASSES
            }
            for channel in C5_CAPTURE_CHANNELS:
                for source_class in C5_FORBIDDEN_SOURCE_CLASSES:
                    projection = next(iter(probe_corpus[source_class]))
                    poisoned = {key: list(values) for key, values in channels.items()}
                    poisoned[channel].append(projection)
                    with self.assertRaises(PrivacyEvidenceError):
                        assert_c5_channels_private(poisoned, probe_corpus)


if __name__ == "__main__":
    unittest.main()
