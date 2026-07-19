from __future__ import annotations

import hashlib
import io
import json
import logging
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.application.hardcover_identity import (
    FixedAdapterPreviewReport,
)
from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_fixed_adapter_preview,
)
from mechanical_librarian.interfaces.cli import main
from tests.support.hardcover_c1_privacy import PrivacyEvidenceError
from tests.support.hardcover_c3_privacy import (
    C3_CAPTURE_CHANNELS,
    assert_channels_private,
    c3_input_variants,
    forbidden_source_corpus,
)


ROOT = Path(__file__).parents[2]


class HardcoverC3PrivacyTests(unittest.TestCase):
    def test_fixed_adapter_channels_are_private_and_metamorphic(self) -> None:
        variants = c3_input_variants(ROOT)
        corpus = forbidden_source_corpus(ROOT, variants=variants)
        channels: dict[str, list[str]] = {
            name: [] for name in C3_CAPTURE_CHANNELS
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

        def hash_new_spy(name: str, value: bytes = b"", **kwargs: object):
            channels["hash_inputs"].append(repr((name, value)))
            return real_hash_new(name, value, **kwargs)

        original_to_json = FixedAdapterPreviewReport.to_json

        def report_spy(report: FixedAdapterPreviewReport) -> str:
            rendered = original_to_json(report)
            channels["exports"].append(rendered)
            channels["reports"].append(rendered)
            return rendered

        outputs: list[str] = []
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    logging.Logger,
                    "_log",
                    side_effect=AssertionError("logging authority reached"),
                )
            )
            stack.enter_context(
                patch.object(FixedAdapterPreviewReport, "to_json", new=report_spy)
            )
            for name in real_hashes:
                stack.enter_context(
                    patch(f"hashlib.{name}", side_effect=hash_spy(name))
                )
            stack.enter_context(patch("hashlib.new", side_effect=hash_new_spy))
            for item in variants:
                output = io.StringIO()
                errors = io.StringIO()
                code = main(
                    [
                        "hardcover-identity-report",
                        "--mode",
                        "synthetic-preview",
                        "--scenario",
                        "fixed-adapter-v3",
                        "--input",
                        "-",
                        "--format",
                        "json",
                    ],
                    state_check_runner=Mock(),
                    hardcover_fixed_adapter_preview_runner=(
                        _run_hardcover_fixed_adapter_preview
                    ),
                    stdin=io.StringIO(json.dumps(item.source)),
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual((code, errors.getvalue()), (0, ""))
                channels["json_stdout"].append(output.getvalue())
                channels["stderr"].append(errors.getvalue())
                outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(channels["hash_inputs"], [])
        self.assertEqual(channels["fingerprint_inputs"], [])
        assert_channels_private(channels, corpus)

        for source_class, projections in corpus.items():
            poison = next(iter(projections))
            for channel in C3_CAPTURE_CHANNELS:
                poisoned = {name: list(values) for name, values in channels.items()}
                poisoned[channel].append(poison)
                isolated = {
                    name: frozenset({poison}) if name == source_class else frozenset()
                    for name in corpus
                }
                with self.subTest(source_class=source_class, channel=channel):
                    with self.assertRaises(PrivacyEvidenceError):
                        assert_channels_private(poisoned, isolated)


if __name__ == "__main__":
    unittest.main()
