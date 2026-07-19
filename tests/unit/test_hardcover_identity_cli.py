from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
import errno
import io
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
    IdentityDecisionPreviewReport,
    InertPreviewReport,
)
from mechanical_librarian.bootstrap import composition as composition_module
from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_identity_decision_preview,
    _run_hardcover_identity_report,
    _run_hardcover_inert_preview,
)
from mechanical_librarian.interfaces.cli import main
from tests.support.hardcover_c1_privacy import (
    ALTERNATE_ISBN,
    CAPTURE_CHANNELS,
    FORBIDDEN_RESULT_KEY_FRAGMENTS,
    PRIVACY_SCAN_PATH,
    REGISTRY_PATH,
    PrivacyEvidenceError,
    assert_channels_private,
    build_scan,
    canonical_bytes,
    c1_input_variants,
    forbidden_source_corpus,
    validate_output_document,
)
from tests.support.hardcover_c2_privacy import (
    C2_CAPTURE_CHANNELS,
    assert_channels_private as assert_c2_channels_private,
    c2_input_variants,
    forbidden_source_corpus as c2_forbidden_source_corpus,
)


ROOT = Path(__file__).parents[2]
EXAMPLE_CONFIG = ROOT / "docs" / "examples" / "synthetic-config.json"


def _exception_chain_metadata(error: BaseException) -> list[str]:
    captured: list[str] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        captured.extend(
            (
                type(current).__qualname__,
                str(current),
                repr(current.args),
                repr(getattr(current, "__notes__", None)),
                repr(current.__suppress_context__),
                repr(traceback.extract_tb(current.__traceback__)),
            )
        )
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return captured


class _AuthorityTrap(AssertionError):
    pass


@contextmanager
def _zero_authority() -> object:
    targets = (
        "mechanical_librarian.bootstrap.composition.HardcoverTitleProvider",
        "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials",
        "mechanical_librarian.bootstrap.composition.httpx.AsyncClient",
        "mechanical_librarian.bootstrap.composition.httpx.Client",
        "mechanical_librarian.bootstrap.composition.httpx.Proxy",
        "mechanical_librarian.bootstrap.composition.httpx.HTTPTransport",
        "mechanical_librarian.bootstrap.composition.httpx.AsyncHTTPTransport",
        "mechanical_librarian.bootstrap.composition.connect",
        "mechanical_librarian.bootstrap.composition.connect_existing",
        "mechanical_librarian.infrastructure.security.private_query.read_private_query",
        "mechanical_librarian.infrastructure.security.private_file.read_private_secret",
        "mechanical_librarian.infrastructure.security.provider_secrets."
        "read_private_secret",
        "mechanical_librarian.infrastructure.security.provider_secrets."
        "PrivateFileProviderCredentials",
        "mechanical_librarian.infrastructure.persistence.sqlite.connect",
        "mechanical_librarian.infrastructure.persistence.sqlite.connect_existing",
        "mechanical_librarian.adapters.providers.hardcover_title."
        "HardcoverTitleProvider",
        "mechanical_librarian.adapters.providers.hardcover_title._system_resolver",
        "socket.getaddrinfo",
        "socket.create_connection",
        "socket.socket",
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(
                patch(
                    target,
                    side_effect=_AuthorityTrap(f"authority reached: {target}"),
                )
            )
        yield


def _merge_patch(target: dict[str, object], update: dict[str, object]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_patch(target[key], value)  # type: ignore[arg-type]
        else:
            target[key] = value


class HardcoverIdentityCliTests(unittest.TestCase):
    def production_argv(self, config: Path) -> list[str]:
        return [
            "hardcover-identity-report",
            "--config",
            str(config),
            "--database",
            "/must-not-open/state.sqlite3",
            "--query-file",
            "/must-not-open/query.json",
            "--format",
            "json",
        ]

    def synthetic_input(
        self, *, title: str = "Synthetic Ebook Alpha"
    ) -> dict[str, object]:
        return {
            "schema": "hardcover-synthetic-input-v3",
            "scenario": "inert-report-v3",
            "config": {
                "enabled": False,
                "secret_reference": None,
                "authority_revision": 1,
            },
            "private_query": {"isbn": "9780000000019", "title": title},
        }

    def identity_row(
        self,
        *,
        edition_id: int = 1001,
        book_id: int = 2001,
        edition_canonical_id: int | None = None,
        book_canonical_id: int | None = None,
        isbn_10: str | None = "0000000019",
        isbn_13: str = "9780000000019",
        title: str = "Synthetic Ebook Alpha",
    ) -> dict[str, object]:
        return {
            "id": edition_id,
            "book_id": book_id,
            "canonical_id": edition_canonical_id,
            "isbn_10": isbn_10,
            "isbn_10_valid": True if isbn_10 is not None else None,
            "isbn_13": isbn_13,
            "isbn_13_valid": True,
            "isbns_match": True,
            "reading_format_id": 4,
            "state": "normalized",
            "compilation": False,
            "book": {
                "id": book_id,
                "canonical_id": book_canonical_id,
                "book_status_id": 1,
                "state": "processed",
                "title": title,
                "parent_book_id": None,
                "compilation": False,
                "is_partial_book": False,
            },
        }

    def identity_input(
        self,
        *,
        title: str = "Synthetic Ebook Alpha",
        rows: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "schema": "hardcover-synthetic-input-v3",
            "scenario": "identity-decision-v3",
            "canonical_isbn_13": "9780000000019",
            "derived_isbn_10": "0000000019",
            "local_title": title,
            "provider_rows": [self.identity_row()] if rows is None else rows,
        }

    def run_identity_preview(
        self, source: object, *, payload: str | None = None
    ) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        exit_code = main(
            [
                "hardcover-identity-report",
                "--mode",
                "synthetic-preview",
                "--scenario",
                "identity-decision-v3",
                "--input",
                "-",
                "--format",
                "json",
            ],
            state_check_runner=Mock(),
            hardcover_identity_decision_preview_runner=(
                _run_hardcover_identity_decision_preview
            ),
            stdin=io.StringIO(json.dumps(source) if payload is None else payload),
            stdout=output,
            stderr=errors,
        )
        return exit_code, output.getvalue(), errors.getvalue()

    def test_identity_decision_preview_is_derived_through_zero_authority_seam(
        self,
    ) -> None:
        source = self.identity_input(
            rows=[
                self.identity_row(),
                self.identity_row(
                    edition_id=1002,
                    book_id=2002,
                    edition_canonical_id=1001,
                    book_canonical_id=2001,
                ),
            ]
        )
        with _zero_authority(), patch.object(
            Path, "read_text", side_effect=_AuthorityTrap("fixture read")
        ), patch.object(
            Path, "read_bytes", side_effect=_AuthorityTrap("fixture read")
        ):
            code, output, errors = self.run_identity_preview(source)
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            json.loads(output),
            {
                "schema": "hardcover-synthetic-output-v5",
                "scenario": "identity-decision-v3",
                "result": {
                    "outcome": "matched",
                    "rows_validated": 2,
                    "aliases_collapsed": 1,
                    "comparison": "equal",
                },
            },
        )
        for forbidden in ("9780000000019", "0000000019", "Synthetic Ebook"):
            self.assertNotIn(forbidden, output)

    def test_composition_constructs_the_provider_adapter(self) -> None:
        source = self.identity_input()
        adapter = composition_module.SyntheticBookIdentityProvider
        with patch.object(
            composition_module,
            "SyntheticBookIdentityProvider",
            wraps=adapter,
        ) as constructor:
            report = _run_hardcover_identity_decision_preview(source)
        constructor.assert_called_once()
        self.assertEqual(
            constructor.call_args.args[0].canonical_isbn_13,
            "9780000000019",
        )
        self.assertEqual(report.outcome.value, "matched")

    def test_identity_variants_cover_permutation_title_979_and_differing_output(
        self,
    ) -> None:
        primary = self.identity_row()
        alias = self.identity_row(
            edition_id=1002,
            book_id=2002,
            edition_canonical_id=1001,
            book_canonical_id=2001,
        )
        _, forward, _ = self.run_identity_preview(
            self.identity_input(rows=[primary, alias])
        )
        _, reverse, _ = self.run_identity_preview(
            self.identity_input(rows=[alias, primary])
        )
        self.assertEqual(forward, reverse)

        _, changed_title, _ = self.run_identity_preview(
            self.identity_input(title="Different Local Title", rows=[primary, alias])
        )
        self.assertEqual(json.loads(changed_title)["result"]["comparison"], "different")
        forward_result = json.loads(forward)["result"]
        changed_result = json.loads(changed_title)["result"]
        self.assertEqual(
            {key: value for key, value in forward_result.items() if key != "comparison"},
            {key: value for key, value in changed_result.items() if key != "comparison"},
        )

        isbn_979 = self.identity_input(
            rows=[self.identity_row(isbn_10=None, isbn_13="9790000000001")]
        )
        isbn_979["canonical_isbn_13"] = "9790000000001"
        isbn_979["derived_isbn_10"] = None
        code, output, errors = self.run_identity_preview(isbn_979)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output)["result"]["outcome"], "matched")

        two_primaries = self.identity_input(
            rows=[primary, self.identity_row(edition_id=1002, book_id=2002)]
        )
        _, different_output, _ = self.run_identity_preview(two_primaries)
        self.assertEqual(json.loads(different_output)["result"]["outcome"], "ambiguous")
        self.assertNotEqual(forward, different_output)

    def test_identity_precedence_and_alias_graph_failures_are_public_results(
        self,
    ) -> None:
        four_rows = [
            self.identity_row(
                edition_id=1000 + index,
                book_id=2000 + index,
                edition_canonical_id=None if index == 1 else 1001,
                book_canonical_id=None if index == 1 else 2001,
            )
            for index in range(1, 5)
        ]
        four_rows[3]["reading_format_id"] = 3
        _, incompatible, _ = self.run_identity_preview(
            self.identity_input(rows=four_rows)
        )
        self.assertEqual(json.loads(incompatible)["result"]["outcome"], "incompatible")

        cycle = [
            self.identity_row(
                edition_canonical_id=1002, book_canonical_id=2002
            ),
            self.identity_row(
                edition_id=1002,
                book_id=2002,
                edition_canonical_id=1001,
                book_canonical_id=2001,
            ),
        ]
        _, malformed, _ = self.run_identity_preview(self.identity_input(rows=cycle))
        self.assertEqual(json.loads(malformed)["result"]["outcome"], "malformed")

    def test_identity_closed_input_failures_are_value_free(self) -> None:
        cases: list[tuple[object, str | None]] = []
        extra = self.identity_input()
        extra["extra"] = True
        cases.append((extra, None))
        missing = self.identity_input()
        del missing["provider_rows"]
        cases.append((missing, None))
        wrong_derived = self.identity_input()
        wrong_derived["derived_isbn_10"] = "0306406152"
        cases.append((wrong_derived, None))
        cases.append(
            (
                {},
                '{"schema":"hardcover-synthetic-input-v3",'
                '"scenario":"identity-decision-v3",'
                '"scenario":"identity-decision-v3"}',
            )
        )
        cases.append(({}, " " * 65_537))
        for source, payload in cases:
            with self.subTest(payload_size=len(payload or "")), _zero_authority():
                code, output, errors = self.run_identity_preview(source, payload=payload)
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertNotIn("978", errors)
            self.assertNotIn("Synthetic", errors)

    def test_identity_privacy_capture_rejects_forbidden_forms_in_every_channel(
        self,
    ) -> None:
        variants = c2_input_variants(ROOT)
        corpus = c2_forbidden_source_corpus(ROOT, variants=variants)
        channels: dict[str, list[str]] = {
            name: [] for name in C2_CAPTURE_CHANNELS
        }
        real_hashes = {
            name: getattr(hashlib, name)
            for name in (
                "md5",
                "sha1",
                "sha224",
                "sha256",
                "sha384",
                "sha512",
                "sha3_256",
                "sha3_512",
                "blake2s",
                "blake2b",
            )
        }
        real_hash_new = hashlib.new

        def hash_spy(name: str):
            def invoke(value: bytes = b"", *args: object, **kwargs: object):
                channels["hash_inputs"].append(repr((name, value)))
                return real_hashes[name](value, *args, **kwargs)

            return invoke

        def hash_new_spy(name: str, value: bytes = b"", **kwargs: object) -> object:
            channels["hash_inputs"].append(repr((name, value)))
            return real_hash_new(name, value, **kwargs)

        def channel_spy(channel: str, failure: str):
            def invoke(*args: object, **kwargs: object) -> None:
                channels[channel].append(repr((args, kwargs)))
                raise _AuthorityTrap(failure)

            return invoke

        def privacy_observer(channel: str, values: tuple[object, ...]) -> None:
            if channel == "metrics":
                name, labels, value, exemplar = values
                for target, observed in (
                    ("metric_names", name),
                    ("metric_labels", labels),
                    ("metric_values", value),
                    ("metric_exemplars", exemplar),
                ):
                    channels[target].append(repr(observed))
                return
            channels[channel].append(repr(values))

        original_to_json = IdentityDecisionPreviewReport.to_json

        def report_spy(report: IdentityDecisionPreviewReport) -> str:
            rendered = original_to_json(report)
            channels["exports"].append(rendered)
            channels["reports"].append(rendered)
            return rendered

        def observed_runner(source: dict[str, object]) -> IdentityDecisionPreviewReport:
            try:
                return _run_hardcover_identity_decision_preview(
                    source, privacy_observer=privacy_observer
                )
            except BaseException as error:
                channels["exceptions"].extend(_exception_chain_metadata(error))
                raise

        argv = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "identity-decision-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]
        persistence_targets = (
            "SQLiteInventoryRepository",
            "SQLiteInventoryV2Repository",
            "SQLiteInventoryPollingRepository",
            "SQLiteEpubOpeningEvidenceRepository",
            "SQLiteTitleProposalRepository",
            "SQLiteTitleReviewCaseRepository",
            "SQLiteJobHeartbeatRepository",
        )
        outputs: list[str] = []
        with ExitStack() as stack:
            stack.enter_context(_zero_authority())
            stack.enter_context(
                patch.object(
                    logging.Logger,
                    "_log",
                    side_effect=channel_spy("structured_logs", "logging reached"),
                )
            )
            stack.enter_context(
                patch(
                    "mechanical_librarian.adapters.persistence.audit."
                    "SQLiteAuditRepository.append",
                    side_effect=channel_spy("audit_logs", "audit reached"),
                )
            )
            stack.enter_context(
                patch.object(IdentityDecisionPreviewReport, "to_json", new=report_spy)
            )
            for name in real_hashes:
                stack.enter_context(
                    patch(f"hashlib.{name}", side_effect=hash_spy(name))
                )
            stack.enter_context(patch("hashlib.new", side_effect=hash_new_spy))
            for target in (
                "mechanical_librarian.bootstrap.composition.Fingerprint",
                "mechanical_librarian.domain.identifiers.Fingerprint",
            ):
                stack.enter_context(
                    patch(
                        target,
                        side_effect=channel_spy(
                            "fingerprint_inputs", "fingerprint reached"
                        ),
                    )
                )
            for name in persistence_targets:
                stack.enter_context(
                    patch(
                        f"mechanical_librarian.bootstrap.composition.{name}",
                        side_effect=channel_spy("database_rows", "database reached"),
                    )
                )

            for variant in variants:
                output = io.StringIO()
                errors = io.StringIO()
                self.assertEqual(
                    main(
                        argv,
                        state_check_runner=Mock(),
                        hardcover_identity_decision_preview_runner=observed_runner,
                        stdin=io.StringIO(variant.input_payload.decode("utf-8")),
                        stdout=output,
                        stderr=errors,
                    ),
                    0,
                )
                channels["json_stdout"].append(output.getvalue())
                channels["stderr"].append(errors.getvalue())
                outputs.append(output.getvalue())

            invalid = copy.deepcopy(variants[0].source)
            invalid["derived_isbn_10"] = "0306406152"
            code, output, errors = self.run_identity_preview(invalid)
            self.assertEqual(code, 2)
            channels["json_stdout"].append(output)
            channels["stderr"].append(errors)
            try:
                observed_runner(invalid)
            except ValueError:
                pass
            else:
                self.fail("invalid private input unexpectedly succeeded")

            text_output = io.StringIO()
            text_errors = io.StringIO()
            self.assertEqual(
                main(
                    [*argv[:-1], "text"],
                    state_check_runner=Mock(),
                    hardcover_identity_decision_preview_runner=observed_runner,
                    stdin=io.StringIO(variants[0].input_payload.decode("utf-8")),
                    stdout=text_output,
                    stderr=text_errors,
                ),
                2,
            )
            channels["text_stdout"].append(text_output.getvalue())
            channels["stderr"].append(text_errors.getvalue())

        self.assertEqual(
            [json.loads(value)["result"]["comparison"] for value in outputs],
            ["equal", "different", "different"],
        )
        for channel in (
            "hash_inputs",
            "fingerprint_inputs",
            "structured_logs",
            "audit_logs",
            "database_rows",
        ):
            self.assertEqual(channels[channel], [], channel)
        for channel in (
            "exceptions",
            "traces",
            "spans",
            "metric_names",
            "metric_labels",
            "metric_values",
            "metric_exemplars",
            "exports",
            "reports",
        ):
            self.assertTrue(channels[channel], channel)

        probes = (
            ("local_title", "WkxDiffe"),
            ("local_title", "PrivateL"),
            ("local_title", "length:31"),
            ("provider_title", "ProviderTitle"),
            ("isbn_identity", "97800000"),
            ("provider_identity", "edition:1001"),
            ("resource_identity", "hardcoveridentity"),
        )
        for source_class, projection in probes:
            self.assertIn(projection, corpus[source_class])
        self.assertIn(
            hashlib.md5(
                variants[0].local_title.encode(), usedforsecurity=False
            ).hexdigest(),
            corpus["local_title"],
        )
        self.assertIn(
            hashlib.sha3_512(variants[0].query_payload).hexdigest(),
            corpus["serialized_private_query"],
        )
        self.assertIn(
            hashlib.blake2b(variants[0].input_payload).hexdigest(),
            corpus["serialized_input"],
        )
        assert_c2_channels_private(channels, corpus)

        incomplete = dict(channels)
        del incomplete[C2_CAPTURE_CHANNELS[-1]]
        with self.assertRaises(PrivacyEvidenceError):
            assert_c2_channels_private(incomplete, corpus)
        incomplete_corpus = dict(corpus)
        del incomplete_corpus["resource_identity"]
        with self.assertRaises(PrivacyEvidenceError):
            assert_c2_channels_private(channels, incomplete_corpus)

        for channel in C2_CAPTURE_CHANNELS:
            for source_class, projections in corpus.items():
                for projection in projections:
                    poisoned = {
                        name: list(values) for name, values in channels.items()
                    }
                    poisoned[channel].append(projection)
                    isolated = {
                        name: frozenset({f"absent-probe:{name}"})
                        for name in corpus
                    }
                    isolated[source_class] = frozenset({projection})
                    with self.assertRaises(PrivacyEvidenceError):
                        assert_c2_channels_private(poisoned, isolated)

        projected_cause = next(iter(corpus["serialized_private_query"]))
        cause = ValueError(projected_cause)
        handled = RuntimeError("handled synthetic identity failure")
        handled.__cause__ = cause
        handled.add_note(projected_cause)
        poisoned = {name: list(values) for name, values in channels.items()}
        poisoned["exceptions"].extend(_exception_chain_metadata(handled))
        with self.assertRaises(PrivacyEvidenceError):
            assert_c2_channels_private(poisoned, corpus)

    def test_production_disabled_and_enabled_short_circuit_before_private_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disabled = root / "disabled.json"
            source = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            disabled.write_text(json.dumps(source), encoding="utf-8")
            enabled = root / "enabled.json"
            source["hardcover_provider"] = {
                "enabled": True,
                "secret_reference": "/must-not-open/hardcover-token",
                "authority_revision": 1,
            }
            enabled.write_text(json.dumps(source), encoding="utf-8")

            for config, outcome in (
                (disabled, "disabled"),
                (enabled, "provider_not_activated"),
            ):
                with self.subTest(outcome=outcome), _zero_authority():
                    output = io.StringIO()
                    errors = io.StringIO()
                    exit_code = main(
                        self.production_argv(config),
                        state_check_runner=Mock(),
                        hardcover_identity_runner=_run_hardcover_identity_report,
                        stdout=output,
                        stderr=errors,
                    )
                    self.assertEqual(exit_code, 0)
                    self.assertEqual(errors.getvalue(), "")
                    self.assertEqual(json.loads(output.getvalue())["outcome"], outcome)
                    self.assertNotIn("must-not-open", output.getvalue())

    def test_real_sigint_while_production_config_read_blocks_propagates(self) -> None:
        script = textwrap.dedent(
            """
            import os
            import signal
            import sys
            import time
            from pathlib import Path

            from mechanical_librarian.interfaces.cli import main

            def blocking_config_runner(config):
                descriptor = os.open(config, os.O_RDONLY | os.O_NONBLOCK)
                try:
                    while True:
                        try:
                            payload = os.read(descriptor, 1)
                        except BlockingIOError:
                            payload = None
                        if payload:
                            raise AssertionError("unexpected synthetic config input")
                        time.sleep(0.01)
                finally:
                    os.close(descriptor)

            config, marker = map(Path, sys.argv[1:])
            signal.signal(signal.SIGINT, signal.default_int_handler)
            marker.write_text("ready", encoding="utf-8")
            try:
                exit_code = main(
                    [
                        "hardcover-identity-report",
                        "--config",
                        str(config),
                        "--database",
                        "/synthetic-unused-state.sqlite3",
                        "--query-file",
                        "/synthetic-unused-query.json",
                        "--format",
                        "json",
                    ],
                    state_check_runner=lambda: None,
                    hardcover_identity_runner=blocking_config_runner,
                )
            except KeyboardInterrupt:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                os.kill(os.getpid(), signal.SIGINT)
                raise AssertionError("default SIGINT did not terminate")
            raise SystemExit(exit_code)
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.fifo"
            marker = root / "ready"
            os.mkfifo(config)
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(config), str(marker)],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            writer: int | None = None
            deadline = time.monotonic() + 5
            try:
                while not marker.exists():
                    if process.poll() is not None:
                        output, errors = process.communicate()
                        self.fail(
                            f"subprocess exited before readiness: {output!r} {errors!r}"
                        )
                    if time.monotonic() >= deadline:
                        self.fail("subprocess did not report readiness")
                    time.sleep(0.01)
                while writer is None:
                    try:
                        writer = os.open(config, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError as error:
                        if error.errno != errno.ENXIO:
                            raise
                        if time.monotonic() >= deadline:
                            self.fail("subprocess did not block on the config FIFO")
                        time.sleep(0.01)
                process.send_signal(signal.SIGINT)
                output, errors = process.communicate(timeout=5)
            finally:
                if writer is not None:
                    os.close(writer)
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            self.assertEqual(process.returncode, -signal.SIGINT)
            self.assertEqual(output, "")
            self.assertEqual(errors, "")

    def test_composition_lookup_authority_traps_are_executable(self) -> None:
        invocations = (
            ("provider", lambda: composition_module.HardcoverTitleProvider(None)),
            (
                "credentials",
                lambda: composition_module.PrivateFileProviderCredentials(),
            ),
            ("client", lambda: composition_module.httpx.AsyncClient()),
            ("proxy", lambda: composition_module.httpx.Proxy("http://invalid")),
            ("transport", lambda: composition_module.httpx.AsyncHTTPTransport()),
            ("database", lambda: composition_module.connect(Path("/must-not-open"))),
        )
        with _zero_authority():
            for name, invoke in invocations:
                with self.subTest(composition_lookup=name):
                    with self.assertRaises(_AuthorityTrap):
                        invoke()

    def test_synthetic_preview_matches_c1_oracle_without_echo_or_stable_identifier(
        self,
    ) -> None:
        argv = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "inert-report-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]
        outputs: list[str] = []
        for title in ("Synthetic Ebook Alpha", "Different Private Title"):
            output = io.StringIO()
            errors = io.StringIO()
            source = self.synthetic_input(title=title)
            exit_code = main(
                argv,
                state_check_runner=Mock(),
                hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                stdin=io.StringIO(json.dumps(source)),
                stdout=output,
                stderr=errors,
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(errors.getvalue(), "")
            self.assertNotIn(title, output.getvalue())
            self.assertNotIn(source["private_query"]["isbn"], output.getvalue())
            self.assertNotIn("sha", output.getvalue().casefold())
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        oracle = (
            ROOT
            / "tests/fixtures/hardcover_identity/c1-inert-report-example-output.json"
        )
        self.assertEqual(outputs[0], oracle.read_text(encoding="utf-8"))

    def test_c1_public_scanner_reproduces_exact_present_tree_evidence(self) -> None:
        fixture_root = ROOT / "tests/fixtures/hardcover_identity"
        expected = {
            "c1-inert-report-input.json": "f9896b56f48d99dd10be69556a95b24b4968beb07b3989517a399d981af66223",
            "c1-inert-report-input-schema.json": "0e275c72d40e2f7e2460d18ce572fea598d79aeff113252a135bc05e8285aa61",
            "c1-inert-report-example-output.json": "910bc6803bbee3c1a43dba545cd497e036ea1ac3f8c663057cb705a4a60e5479",
            "c1-inert-report-variant-matrix.json": "e18f2eb3dcd75c09cb57443da8e2bea666604813c94a922260de7e92b30b9bf4",
            "c1-inert-report-output-schema-v1.json": "602c2037fa7277ee428f7a681f1f34d2a834203e9c12e61713fd85840c30b584",
            "c1-inert-report-privacy-contract-v1.json": "75a6be84afab11ae5a154415960108f15f9db3e34ffd8a2a22cc23edc2c68da3",
            "c1-inert-report-privacy-scan-v1.json": "df030bb495712da448dccc896765dc2fad4ce893666da2bdd22c87e054fcf726",
            "c1-inert-report-registry-v1.json": "eb3ec8a5454a3e4a21f9bbfe9b8b2308687de00f6a95df7533055a6083009619",
            "c2-identity-decision-input.json": "c6ed8d6ad0253af139e07164668bb49a6763d8e979dd21fcd9e638a9861129fb",
            "c2-identity-decision-example-output.json": "319744d9311c6c6b43ea104dbc90846a23be3eea53a0f9f7e6bdba1c7aaff779",
            "c2-identity-decision-input-schema.json": "daab0b30e32cf20ebf631e5cb97e6e94f51243867d45377882acf757cf1ea36e",
            "c2-identity-decision-variant-matrix.json": "d3cb1432d994e18527fdf354a6882d6d57b92d1358f2cfd745070dbccb529359",
            "c2-preview-output-schema-v1.json": "0ff4003920471f37661dded39e2400ac0944a0edea13be9a5d8df49ffc037991",
            "c2-preview-output-privacy-contract-v1.json": "315984420942e5819c760391b371d452ee5be178c1b30085effe1d8d4e81d47d",
            "c2-preview-output-privacy-scan-v1.json": "81a0aad7d18f16470b13a8533df0d6a5192e056576aed211509aae9d33383452",
            "preview-registry-c2-v6.json": "557dfdf0300bf2d59fa5571fd3af2136fd4f6099e14b1f1d1b953b0cb56a9d11",
            "c3-fixed-adapter-input.json": "8944fd8edda4c4e2907ddb845d1ab3ccb833bfe1016d031f0497203ac44f229a",
            "c3-fixed-adapter-example-output.json": "09bbf56b45a896c1a4a8ec0ef2ac45a3863d2e55370e901693677de7209baa40",
            "c3-fixed-adapter-input-schema.json": "0cfb8b483f62bb410a47cb88a7e5b2b4785f339df65a03c3f7706167dd633f42",
            "c3-fixed-adapter-variant-matrix.json": "207dcef3ceccdace6addda731055b60e40d0688d0c920a703f5f828b30f472b4",
            "c3-preview-output-schema-v1.json": "40980c861683de4f56b8fb7fa60a769a47ea1223c798c858c79fd876d7f694da",
            "c3-preview-output-privacy-contract-v1.json": "32ec6078b981b15d3255296cb0f41346b5840686ae859ea48176cdb047895b37",
            "c3-preview-output-privacy-scan-v1.json": "bedc4f12909da3b255a81e83537bf0019859f393fb9c460085411f9e30df021b",
            "preview-registry-c3-v6.json": "ff6c3c68fee3cb4f7712bdb650e0685c8fc4b4d345a573c195e7904863f65943",
        }
        self.assertEqual(
            {path.name for path in fixture_root.glob("*.json")}, set(expected)
        )
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha256((fixture_root / name).read_bytes()).hexdigest(),
                    digest,
                )
        generated = canonical_bytes(build_scan(ROOT))
        committed = (ROOT / PRIVACY_SCAN_PATH).read_bytes()
        self.assertEqual(generated, committed)
        self.assertEqual(
            hashlib.sha256(generated).hexdigest(),
            expected[PRIVACY_SCAN_PATH.name],
        )
        registry = json.loads((ROOT / REGISTRY_PATH).read_text(encoding="utf-8"))
        self.assertEqual(
            registry["resources"]["privacy_scan"]["sha256"],
            hashlib.sha256(generated).hexdigest(),
        )
        self.assertFalse(registry["scope"]["future_scenarios_included"])
        future_markers = (
            "identity-decision-v3",
            "fixed-adapter-v3",
            "durable-work-v3",
            "safe-executor-v3",
            "schema8",
        )
        for name in (
            "c1-inert-report-output-schema-v1.json",
            "c1-inert-report-privacy-contract-v1.json",
            "c1-inert-report-privacy-scan-v1.json",
            "c1-inert-report-registry-v1.json",
        ):
            payload = (fixture_root / name).read_text(encoding="utf-8").casefold()
            for marker in future_markers:
                self.assertNotIn(marker, payload)

    def test_c2_registry_binds_only_installed_c1_and_c2_resources(self) -> None:
        fixture_root = ROOT / "tests/fixtures/hardcover_identity"
        output_schema = json.loads(
            (fixture_root / "c2-preview-output-schema-v1.json").read_text(
                encoding="utf-8"
            )
        )
        inert_properties = output_schema["$defs"]["inert_report_result"][
            "properties"
        ]
        for key in (
            "private_file_opened",
            "database_opened",
            "secret_opened",
            "network_started",
        ):
            self.assertEqual(inert_properties[key], {"const": False})

        branches = output_schema["$defs"]["identity_decision_result"]["oneOf"]

        def signature(branch: dict[str, object]) -> tuple[object, ...]:
            properties = branch["properties"]
            return (
                properties["outcome"],
                properties["rows_validated"],
                properties["aliases_collapsed"],
                properties["comparison"],
                branch["additionalProperties"],
                branch["type"],
            )

        integer = lambda minimum, maximum: {
            "maximum": maximum,
            "minimum": minimum,
            "type": "integer",
        }
        expected_signatures = (
            (
                {"const": "matched"},
                {"const": 1},
                {"const": 0},
                {"enum": ["equal", "different"]},
                False,
                "object",
            ),
            (
                {"const": "matched"},
                {"const": 2},
                {"const": 1},
                {"enum": ["equal", "different"]},
                False,
                "object",
            ),
            (
                {"const": "matched"},
                {"const": 3},
                {"const": 2},
                {"enum": ["equal", "different"]},
                False,
                "object",
            ),
            (
                {"const": "no_match"},
                {"const": 0},
                {"const": 0},
                {"const": "not_compared"},
                False,
                "object",
            ),
            (
                {"const": "ambiguous"},
                integer(2, 4),
                {"const": 0},
                {"const": "not_compared"},
                False,
                "object",
            ),
            (
                {"const": "incompatible"},
                integer(0, 4),
                {"const": 0},
                {"const": "not_compared"},
                False,
                "object",
            ),
            (
                {"const": "malformed"},
                integer(0, 4),
                {"const": 0},
                {"const": "not_compared"},
                False,
                "object",
            ),
        )
        self.assertEqual(
            tuple(signature(branch) for branch in branches), expected_signatures
        )
        for branch in branches:
            self.assertEqual(
                branch["required"],
                ["outcome", "rows_validated", "aliases_collapsed", "comparison"],
            )
        registry = json.loads(
            (fixture_root / "preview-registry-c2-v6.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            registry["predecessor_registry_sha256"],
            "eb3ec8a5454a3e4a21f9bbfe9b8b2308687de00f6a95df7533055a6083009619",
        )
        self.assertEqual(
            [scenario["scenario"] for scenario in registry["scenarios"]],
            ["inert-report-v3", "identity-decision-v3"],
        )
        bindings = (
            registry["output_schema"],
            registry["output_privacy_contract"],
            registry["output_privacy_scan"],
            *(
                resource
                for scenario in registry["scenarios"]
                for resource in scenario["resources"].values()
            ),
        )
        for binding in bindings:
            path = fixture_root / binding["path"]
            with self.subTest(path=path.name):
                payload = path.read_bytes()
                self.assertEqual(len(payload), binding["bytes"])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), binding["sha256"])
        for name in (
            "c2-preview-output-schema-v1.json",
            "c2-preview-output-privacy-contract-v1.json",
            "c2-preview-output-privacy-scan-v1.json",
            "preview-registry-c2-v6.json",
        ):
            payload = (fixture_root / name).read_text(encoding="utf-8")
            for marker in ("fixed-adapter-v3", "durable-work-v3", "safe-executor-v3"):
                self.assertNotIn(marker, payload)

        source = json.loads(
            (fixture_root / "c2-identity-decision-input.json").read_text(
                encoding="utf-8"
            )
        )
        code, output, errors = self.run_identity_preview(source)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(
            output,
            (fixture_root / "c2-identity-decision-example-output.json").read_text(
                encoding="utf-8"
            ),
        )

    def test_c1_generator_runs_directly_from_repository_root(self) -> None:
        paths = tuple(
            sorted((ROOT / "tests/fixtures/hardcover_identity").glob("*.json"))
        )
        before = {path: path.read_bytes() for path in paths}
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        completed = subprocess.run(
            ["python3", "scripts/generate_hardcover_c1_privacy.py"],
            cwd=ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            {path: path.read_bytes() for path in paths},
            before,
        )

    def test_c1_output_schema_rejects_forbidden_and_additional_properties(
        self,
    ) -> None:
        fixture_root = ROOT / "tests/fixtures/hardcover_identity"
        output = json.loads(
            (fixture_root / "c1-inert-report-example-output.json").read_text()
        )
        validate_output_document(output)
        for fragment in FORBIDDEN_RESULT_KEY_FRAGMENTS:
            mutated = copy.deepcopy(output)
            mutated["result"][f"{fragment}_sentinel"] = False
            with self.subTest(forbidden_result_fragment=fragment):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for location in ("top", "result"):
            mutated = copy.deepcopy(output)
            target = mutated if location == "top" else mutated["result"]
            target["unexpected"] = False
            with self.subTest(additional_property=location):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for key in tuple(output):
            mutated = copy.deepcopy(output)
            del mutated[key]
            with self.subTest(missing_top_level_key=key):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for key in tuple(output["result"]):
            mutated = copy.deepcopy(output)
            del mutated["result"][key]
            with self.subTest(missing_result_key=key):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        for key, value in (
            ("schema", "hardcover-synthetic-output-v6"),
            ("scenario", "another-scenario"),
        ):
            mutated = copy.deepcopy(output)
            mutated[key] = value
            with self.subTest(mutated_identity=key):
                with self.assertRaises(PrivacyEvidenceError):
                    validate_output_document(mutated)
        mutated = copy.deepcopy(output)
        mutated["result"]["outcome"] = "provider_not_activated"
        with self.assertRaises(PrivacyEvidenceError):
            validate_output_document(mutated)
        for key in (
            "private_file_opened",
            "database_opened",
            "secret_opened",
            "network_started",
        ):
            for value in (True, 0, 1, None, "false"):
                mutated = copy.deepcopy(output)
                mutated["result"][key] = value
                with self.subTest(counter=key, malformed_value=value):
                    with self.assertRaises(PrivacyEvidenceError):
                        validate_output_document(mutated)

    def test_retained_c1_variant_matrix_executes_through_real_cli_seam(self) -> None:
        matrix = json.loads(
            (
                ROOT
                / "tests/fixtures/hardcover_identity/c1-inert-report-variant-matrix.json"
            ).read_text(encoding="utf-8")
        )
        argv = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "inert-report-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]
        self.assertEqual(matrix["scenario"], "inert-report-v3")
        for case in matrix["cases"]:
            with self.subTest(case=case["id"]), _zero_authority():
                source = copy.deepcopy(self.synthetic_input())
                update = copy.deepcopy(case["input_patch"])
                private_update = update.get("private_query")
                if isinstance(private_update, dict) and isinstance(
                    private_update.get("title_generator"), dict
                ):
                    generator = private_update.pop("title_generator")
                    private_update["title"] = generator["scalar"] * generator["size"]
                _merge_patch(source, update)
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                    stdin=io.StringIO(json.dumps(source)),
                    stdout=output,
                    stderr=errors,
                )
                result = json.loads(output.getvalue())["result"]
                self.assertEqual(errors.getvalue(), "")
                self.assertEqual(result["outcome"], case["expected"]["outcome"])
                self.assertEqual(exit_code, 0 if result["outcome"] == "disabled" else 2)
                for counter in (
                    "private_file_opened",
                    "database_opened",
                    "secret_opened",
                    "network_started",
                ):
                    self.assertFalse(result[counter])

    def test_c1_input_variants_are_channel_complete_private_and_metamorphic(
        self,
    ) -> None:
        variants = c1_input_variants(ROOT)
        corpus = forbidden_source_corpus(ROOT, variants=variants)
        channels: dict[str, list[str]] = {name: [] for name in CAPTURE_CHANNELS}
        real_hashes = {
            "md5": hashlib.md5,
            "sha1": hashlib.sha1,
            "sha224": hashlib.sha224,
            "sha256": hashlib.sha256,
            "sha384": hashlib.sha384,
            "sha512": hashlib.sha512,
            "sha3_256": hashlib.sha3_256,
            "sha3_512": hashlib.sha3_512,
            "blake2s": hashlib.blake2s,
            "blake2b": hashlib.blake2b,
        }
        real_hash_new = hashlib.new

        def hash_spy(name: str):
            def invoke(value: bytes = b"", *args: object, **kwargs: object):
                channels["hash_inputs"].append(repr(value))
                return real_hashes[name](value, *args, **kwargs)

            return invoke

        def hash_new_spy(
            name: str, value: bytes = b"", **kwargs: object
        ) -> object:
            channels["hash_inputs"].append(repr((name, value)))
            return real_hash_new(name, value, **kwargs)

        def log_spy(*args: object, **kwargs: object) -> None:
            channels["structured_logs"].append(repr((args, kwargs)))

        def audit_spy(*args: object, **kwargs: object) -> None:
            channels["audit_logs"].append(repr((args, kwargs)))
            raise _AuthorityTrap("audit persistence reached")

        def persistence_spy(*args: object, **kwargs: object) -> None:
            channels["non_audit_persistence"].append(repr((args, kwargs)))
            raise _AuthorityTrap("non-audit persistence reached")

        def fingerprint_spy(*args: object, **kwargs: object) -> None:
            channels["fingerprint_inputs"].append(repr((args, kwargs)))
            raise _AuthorityTrap("fingerprint reached")

        def privacy_observer(channel: str, values: tuple[object, ...]) -> None:
            if channel == "metrics":
                name, labels, value, exemplar = values
                channels["metric_names"].append(repr(name))
                channels["metric_labels"].append(repr(labels))
                channels["metric_values"].append(repr(value))
                channels["metric_exemplars"].append(repr(exemplar))
                return
            channels[channel].append(repr(values))

        original_to_json = InertPreviewReport.to_json

        def report_spy(report: InertPreviewReport) -> str:
            rendered = original_to_json(report)
            channels["exports"].append(rendered)
            channels["reports"].append(rendered)
            return rendered

        def observed_runner(source: dict[str, object]) -> InertPreviewReport:
            try:
                return _run_hardcover_inert_preview(
                    source,
                    privacy_observer=privacy_observer,
                )
            except BaseException as error:
                channels["exceptions"].extend(_exception_chain_metadata(error))
                raise

        argv = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "inert-report-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]
        outputs: list[str] = []
        persistence_targets = (
            "mechanical_librarian.bootstrap.composition.SQLiteInventoryRepository",
            "mechanical_librarian.bootstrap.composition.SQLiteInventoryV2Repository",
            "mechanical_librarian.bootstrap.composition."
            "SQLiteInventoryPollingRepository",
            "mechanical_librarian.bootstrap.composition."
            "SQLiteEpubOpeningEvidenceRepository",
            "mechanical_librarian.bootstrap.composition.SQLiteTitleProposalRepository",
            "mechanical_librarian.bootstrap.composition."
            "SQLiteTitleReviewCaseRepository",
            "mechanical_librarian.bootstrap.composition.SQLiteJobHeartbeatRepository",
        )
        with ExitStack() as stack:
            stack.enter_context(_zero_authority())
            stack.enter_context(
                patch.object(logging.Logger, "_log", side_effect=log_spy)
            )
            stack.enter_context(
                patch(
                    "mechanical_librarian.adapters.persistence.audit."
                    "SQLiteAuditRepository.append",
                    side_effect=audit_spy,
                )
            )
            stack.enter_context(
                patch.object(InertPreviewReport, "to_json", new=report_spy)
            )
            for name in real_hashes:
                stack.enter_context(
                    patch(
                        f"hashlib.{name}",
                        side_effect=hash_spy(name),
                    )
                )
            stack.enter_context(patch("hashlib.new", side_effect=hash_new_spy))
            stack.enter_context(
                patch(
                    "mechanical_librarian.bootstrap.composition.Fingerprint",
                    side_effect=fingerprint_spy,
                )
            )
            for target in persistence_targets:
                stack.enter_context(patch(target, side_effect=persistence_spy))

            for variant in variants:
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=observed_runner,
                    stdin=io.StringIO(variant.input_payload.decode("utf-8")),
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, 0)
                channels["stdout"].append(output.getvalue())
                channels["stderr"].append(errors.getvalue())
                outputs.append(output.getvalue())

            invalid = copy.deepcopy(variants[0].source)
            invalid["unexpected"] = True
            invalid_output = io.StringIO()
            invalid_errors = io.StringIO()
            self.assertEqual(
                main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=observed_runner,
                    stdin=io.StringIO(json.dumps(invalid)),
                    stdout=invalid_output,
                    stderr=invalid_errors,
                ),
                2,
            )
            channels["stdout"].append(invalid_output.getvalue())
            channels["stderr"].append(invalid_errors.getvalue())

        self.assertEqual(outputs, [outputs[0]] * len(variants))
        self.assertEqual(channels["hash_inputs"], [])
        self.assertEqual(channels["fingerprint_inputs"], [])
        self.assertEqual(channels["structured_logs"], [])
        self.assertEqual(channels["audit_logs"], [])
        self.assertEqual(channels["non_audit_persistence"], [])
        self.assertTrue(channels["exceptions"])
        self.assertTrue(channels["traces"])
        self.assertTrue(channels["spans"])
        self.assertTrue(channels["metric_names"])
        self.assertTrue(channels["metric_labels"])
        self.assertTrue(channels["metric_values"])
        self.assertTrue(channels["metric_exemplars"])
        self.assertTrue(channels["exports"])
        self.assertTrue(channels["reports"])
        self.assertEqual(
            tuple(variant.name for variant in variants),
            ("canonical", "title-only-alternate", "isbn-alternate"),
        )
        self.assertEqual(variants[2].raw_isbn, ALTERNATE_ISBN)
        self.assertEqual(variants[2].canonical_isbn, "9780306406157")
        for variant in variants:
            self.assertIn(
                variant.query_payload.decode("utf-8"),
                corpus["serialized_private_query"],
            )
            self.assertIn(
                variant.input_payload.decode("utf-8"),
                corpus["serialized_input"],
            )
        self.assertIn(
            hashlib.md5(
                variants[0].title.encode("utf-8"), usedforsecurity=False
            ).hexdigest(),
            corpus["title"],
        )
        self.assertIn(
            hashlib.sha1(
                variants[0].title.encode("utf-8"), usedforsecurity=False
            ).hexdigest(),
            corpus["title"],
        )
        assert_channels_private(channels, corpus)
        incomplete_channels = dict(channels)
        del incomplete_channels[CAPTURE_CHANNELS[-1]]
        with self.assertRaises(PrivacyEvidenceError):
            assert_channels_private(incomplete_channels, corpus)
        incomplete_corpus = dict(corpus)
        del incomplete_corpus["resource_identity"]
        with self.assertRaises(PrivacyEvidenceError):
            assert_channels_private(channels, incomplete_corpus)

        hand_authored_mutations = (
            ("title", "WKXDIFFERENTCONFIDENTIALNAME91"),
            ("canonical_isbn", "9780306406157"),
            (
                "resource_identity",
                "dGVzdHMvZml4dHVyZXMvaGFyZGNvdmVyX2lkZW50aXR5L2MxLWluZXJ0"
                "LXJlcG9ydC1yZWdpc3RyeS12MS5qc29u",
            ),
            (
                "serialized_input",
                "33f0bddb9114b8a2a8c13269f09e6c1107f723836cd4ac3e6087e1131a46266d",
            ),
        )
        for source_class, projection in hand_authored_mutations:
            self.assertIn(projection, corpus[source_class])
            poisoned = {name: list(values) for name, values in channels.items()}
            poisoned["stdout"].append(projection)
            with self.subTest(hand_authored_source_class=source_class):
                with self.assertRaises(PrivacyEvidenceError):
                    assert_channels_private(poisoned, corpus)

        discriminating_chunk_probes = (
            ("stdout", "title", "WkxDiffe"),
            ("structured_logs", "title", "WkxDifferent"),
            ("traces", "title", "Confidential"),
            ("reports", "title", "ConfidentialName"),
            ("exceptions", "canonical_isbn", "97803064"),
            ("audit_logs", "resource_identity", "hardcoveridentity"),
        )
        for channel, source_class, projection in discriminating_chunk_probes:
            self.assertIn(projection, corpus[source_class])
            poisoned = {name: list(values) for name, values in channels.items()}
            poisoned[channel].append(projection)
            with self.subTest(
                discriminating_channel=channel,
                discriminating_source_class=source_class,
                discriminating_projection=projection,
            ):
                with self.assertRaises(PrivacyEvidenceError):
                    assert_channels_private(poisoned, corpus)

        for channel in CAPTURE_CHANNELS:
            for source_class, projections in corpus.items():
                for projection in projections:
                    poisoned = {
                        name: list(values) for name, values in channels.items()
                    }
                    poisoned[channel].append(projection)
                    probe_corpus = {
                        name: frozenset({f"absent-probe:{name}"})
                        for name in corpus
                    }
                    probe_corpus[source_class] = frozenset({projection})
                    with self.subTest(
                        negative_scanner_channel=channel,
                        source_class=source_class,
                    ):
                        with self.assertRaises(PrivacyEvidenceError):
                            assert_channels_private(poisoned, probe_corpus)

        projected_cause = next(iter(corpus["serialized_private_query"]))
        cause = ValueError(projected_cause)
        handled = RuntimeError("handled synthetic preview failure")
        handled.__cause__ = cause
        handled.add_note(projected_cause)
        poisoned = {name: list(values) for name, values in channels.items()}
        poisoned["exceptions"].extend(_exception_chain_metadata(handled))
        with self.assertRaises(PrivacyEvidenceError):
            assert_channels_private(poisoned, corpus)

    def test_synthetic_invalid_input_is_typed_and_zero_access(self) -> None:
        source = self.synthetic_input()
        source["unexpected"] = True
        output = io.StringIO()
        errors = io.StringIO()
        with _zero_authority():
            exit_code = main(
                [
                    "hardcover-identity-report",
                    "--mode",
                    "synthetic-preview",
                    "--scenario",
                    "inert-report-v3",
                    "--input",
                    "-",
                    "--format",
                    "json",
                ],
                state_check_runner=Mock(),
                hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                stdin=io.StringIO(json.dumps(source)),
                stdout=output,
                stderr=errors,
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(
            json.loads(output.getvalue())["result"],
            {
                "outcome": "invalid_input",
                "private_file_opened": False,
                "database_opened": False,
                "secret_opened": False,
                "network_started": False,
            },
        )

    def test_valid_and_invalid_synthetic_cli_are_zero_authority(self) -> None:
        argv = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "inert-report-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]
        for source, expected_exit in (
            (self.synthetic_input(), 0),
            ({**self.synthetic_input(), "unexpected": True}, 2),
        ):
            with self.subTest(expected_exit=expected_exit), _zero_authority():
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    argv,
                    state_check_runner=Mock(),
                    hardcover_inert_preview_runner=_run_hardcover_inert_preview,
                    stdin=io.StringIO(json.dumps(source)),
                    stdout=output,
                    stderr=errors,
                )
            self.assertEqual(exit_code, expected_exit)
            self.assertEqual(errors.getvalue(), "")

    def test_unknown_production_result_fails_closed(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        runner = Mock(
            return_value=HardcoverIdentityReport(HardcoverIdentityOutcome.INVALID_INPUT)
        )
        exit_code = main(
            self.production_argv(EXAMPLE_CONFIG),
            state_check_runner=Mock(),
            hardcover_identity_runner=runner,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "Hardcover identity report failed safely.\n")

    def test_malformed_exact_type_synthetic_reports_fail_without_serialization(
        self,
    ) -> None:
        argv = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "inert-report-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]
        malformed = [
            InertPreviewReport("disabled"),  # type: ignore[arg-type]
            InertPreviewReport(HardcoverIdentityOutcome.PROVIDER_NOT_ACTIVATED),
        ]
        for counter in (
            "private_file_opened",
            "database_opened",
            "secret_opened",
            "network_started",
        ):
            for value in (True, 0, 1, None, "false"):
                malformed.append(
                    InertPreviewReport(
                        HardcoverIdentityOutcome.DISABLED,
                        **{counter: value},  # type: ignore[arg-type]
                    )
                )
        for report in malformed:
            output = io.StringIO()
            errors = io.StringIO()
            exit_code = main(
                argv,
                state_check_runner=Mock(),
                hardcover_inert_preview_runner=Mock(return_value=report),
                stdin=io.StringIO(json.dumps(self.synthetic_input())),
                stdout=output,
                stderr=errors,
            )
            with self.subTest(report=report):
                self.assertEqual(exit_code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(
                    errors.getvalue(),
                    "Hardcover identity report failed safely.\n",
                )


if __name__ == "__main__":
    unittest.main()
