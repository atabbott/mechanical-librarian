from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.adapters.providers import hardcover_identity as adapter
from mechanical_librarian.adapters.providers.hardcover_identity import (
    StructuralHardcoverIdentityPreview,
)
from mechanical_librarian.application.hardcover_identity import (
    AddressPolicyOutcome,
    FixedAdapterOutcome,
    FixedAdapterPreviewReport,
)
from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_fixed_adapter_preview,
)
from mechanical_librarian.interfaces.cli import main


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/hardcover_identity"


class _AuthorityTrap(AssertionError):
    pass


class HardcoverFixedAdapterCliTests(unittest.TestCase):
    def source(self) -> dict[str, object]:
        return json.loads(
            (FIXTURES / "c3-fixed-adapter-input.json").read_text(encoding="utf-8")
        )

    def run_preview(
        self, source: object, *, payload: str | None = None
    ) -> tuple[int, str, str]:
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
            stdin=io.StringIO(json.dumps(source) if payload is None else payload),
            stdout=output,
            stderr=errors,
        )
        return code, output.getvalue(), errors.getvalue()

    def authority_traps(self) -> ExitStack:
        stack = ExitStack()
        targets = (
            "socket.socket",
            "socket.create_connection",
            "socket.getaddrinfo",
            "httpx.Client",
            "httpx.AsyncClient",
            "mechanical_librarian.infrastructure.security.provider_secrets."
            "PrivateFileProviderCredentials",
            "mechanical_librarian.infrastructure.security.provider_secrets."
            "read_private_secret",
        )
        for target in targets:
            stack.enter_context(
                patch(target, side_effect=_AuthorityTrap("authority reached"))
            )
        return stack

    def test_exact_fixture_runs_real_adapter_and_matches_oracle_without_egress(
        self,
    ) -> None:
        source = self.source()
        with self.authority_traps(), patch.object(
            Path, "read_bytes", side_effect=_AuthorityTrap("fixture read")
        ), patch.object(Path, "read_text", side_effect=_AuthorityTrap("fixture read")):
            code, output, errors = self.run_preview(source)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(
            output,
            (FIXTURES / "c3-fixed-adapter-example-output.json").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual(
            json.loads(output)["result"],
            {
                "actual_network": False,
                "address_policy": "accepted",
                "outcome": "matched",
                "peer_equal": True,
                "row_count": 2,
            },
        )

    def test_address_status_and_empty_editions_derive_distinct_outputs(self) -> None:
        source = self.source()
        for address in ("10.0.0.1", "127.0.0.1", "192.0.2.44"):
            with self.subTest(address=address):
                changed = copy.deepcopy(source)
                changed["transport"]["selected_address"] = address
                changed["transport"]["connected_peer_address"] = address
                code, output, errors = self.run_preview(changed)
                self.assertEqual((code, errors), (0, ""))
                self.assertEqual(
                    json.loads(output)["result"],
                    {
                        "actual_network": False,
                        "address_policy": "rejected",
                        "outcome": "incompatible",
                        "peer_equal": False,
                        "row_count": 0,
                    },
                )

        unauthorized = copy.deepcopy(source)
        unauthorized["response"]["raw_status_line_bytes_utf8"] = (
            "HTTP/1.1 401 Unauthorized\r\n"
        )
        unauthorized["response"]["raw_header_lines_bytes_utf8"] = []
        unauthorized["response"]["raw_body_bytes_utf8"] = "not-json"
        _, output, _ = self.run_preview(unauthorized)
        self.assertEqual(json.loads(output)["result"]["outcome"], "authentication")

        empty = copy.deepcopy(source)
        empty_body = '{"data":{"editions":[]}}'
        empty["response"]["raw_body_bytes_utf8"] = empty_body
        empty["response"]["raw_header_lines_bytes_utf8"] = [
            "Content-Type: application/json\r\n",
            f"Content-Length: {len(empty_body.encode())}\r\n",
        ]
        _, output, _ = self.run_preview(empty)
        self.assertEqual(json.loads(output)["result"]["outcome"], "no_match")
        self.assertEqual(json.loads(output)["result"]["row_count"], 0)

    def test_noncanonical_public_ipv6_uses_validated_channel_address(self) -> None:
        expanded = "2001:4860:4860:0000:0000:0000:0000:8888"
        compressed = "2001:4860:4860::8888"
        for peer in (expanded, compressed):
            with self.subTest(peer=peer):
                source = self.source()
                source["transport"]["selected_address"] = expanded
                source["transport"]["connected_peer_address"] = peer
                code, output, errors = self.run_preview(source)
                self.assertEqual((code, errors), (0, ""))
                self.assertEqual(
                    json.loads(output)["result"],
                    {
                        "actual_network": False,
                        "address_policy": "accepted",
                        "outcome": "matched",
                        "peer_equal": True,
                        "row_count": 2,
                    },
                )

    def test_row_permutation_and_four_row_validation_precede_selection(self) -> None:
        source = self.source()
        document = json.loads(source["response"]["raw_body_bytes_utf8"])
        document["data"]["editions"].reverse()
        permuted_body = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        source["response"]["raw_body_bytes_utf8"] = permuted_body
        source["response"]["raw_header_lines_bytes_utf8"][-1] = (
            f"Content-Length: {len(permuted_body.encode())}\r\n"
        )
        _, permuted, _ = self.run_preview(source)
        self.assertEqual(
            permuted,
            (FIXTURES / "c3-fixed-adapter-example-output.json").read_text(
                encoding="utf-8"
            ),
        )

        four = self.source()
        document = json.loads(four["response"]["raw_body_bytes_utf8"])
        primary = document["data"]["editions"][0]
        rows = [copy.deepcopy(primary) for _ in range(4)]
        for index, row in enumerate(rows, start=1):
            row["id"] = 1000 + index
            row["book_id"] = 2000 + index
            row["book"]["id"] = 2000 + index
            row["canonical_id"] = None if index == 1 else 1001
            row["book"]["canonical_id"] = None if index == 1 else 2001
        rows[-1]["reading_format_id"] = 3
        document["data"]["editions"] = rows
        four_body = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        four["response"]["raw_body_bytes_utf8"] = four_body
        four["response"]["raw_header_lines_bytes_utf8"][-1] = (
            f"Content-Length: {len(four_body.encode())}\r\n"
        )
        _, output, _ = self.run_preview(four)
        self.assertEqual(json.loads(output)["result"]["outcome"], "incompatible")
        self.assertEqual(json.loads(output)["result"]["row_count"], 4)

    def test_closed_input_failures_are_value_free(self) -> None:
        cases: list[tuple[object, str | None]] = []
        extra = self.source()
        extra["extra"] = True
        cases.append((extra, None))
        missing = self.source()
        del missing["transport"]
        cases.append((missing, None))
        cases.append(({}, ""))
        cases.append(({}, " " * 65_537))
        for source, payload in cases:
            with self.subTest(payload_size=len(payload or "")), self.authority_traps():
                code, output, errors = self.run_preview(source, payload=payload)
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertEqual(errors, "Hardcover identity report failed safely.\n")

    def test_keyboard_interrupt_propagates_from_input_runner_and_render(self) -> None:
        arguments = [
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "fixed-adapter-v3",
            "--input",
            "-",
            "--format",
            "json",
        ]

        class InterruptedInput(io.StringIO):
            def read(self, size: int = -1) -> str:
                raise KeyboardInterrupt

        report = FixedAdapterPreviewReport(
            FixedAdapterOutcome.MATCHED,
            1,
            True,
            False,
            AddressPolicyOutcome.ACCEPTED,
        )
        cases = (
            ("input", InterruptedInput(), Mock(return_value=report), None),
            (
                "runner",
                io.StringIO(json.dumps(self.source())),
                Mock(side_effect=KeyboardInterrupt),
                None,
            ),
            (
                "render",
                io.StringIO(json.dumps(self.source())),
                Mock(return_value=report),
                patch.object(
                    FixedAdapterPreviewReport,
                    "to_json",
                    side_effect=KeyboardInterrupt,
                ),
            ),
        )
        for phase, input_stream, runner, render_patch in cases:
            with self.subTest(phase=phase):
                output = io.StringIO()
                errors = io.StringIO()
                context = render_patch if render_patch is not None else ExitStack()
                with context, self.assertRaises(KeyboardInterrupt):
                    main(
                        arguments,
                        state_check_runner=Mock(),
                        hardcover_fixed_adapter_preview_runner=runner,
                        stdin=input_stream,
                        stdout=output,
                        stderr=errors,
                    )
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(errors.getvalue(), "")

    def test_fixed_transport_text_boundaries_are_exact(self) -> None:
        for size, accepted in ((127, True), (128, True), (129, False)):
            with self.subTest(status_line_bytes=size):
                source = self.source()
                prefix = "HTTP/1.1 200 "
                source["response"]["raw_status_line_bytes_utf8"] = (
                    prefix + "A" * (size - len(prefix) - 2) + "\r\n"
                )
                code, output, errors = self.run_preview(source)
                self.assertEqual(code, 0 if accepted else 2)
                self.assertEqual(bool(output), accepted)
                self.assertEqual(errors, "" if accepted else "Hardcover identity report failed safely.\n")

        for count, accepted in ((15, True), (16, True), (17, False)):
            with self.subTest(header_count=count):
                source = self.source()
                headers = source["response"]["raw_header_lines_bytes_utf8"]
                headers[:0] = [
                    f"X-Synthetic-{index}: A\r\n"
                    for index in range(count - len(headers))
                ]
                code, output, _ = self.run_preview(source)
                self.assertEqual(code, 0 if accepted else 2)
                self.assertEqual(bool(output), accepted)

        for size, accepted in ((255, True), (256, True), (257, False)):
            with self.subTest(header_line_bytes=size):
                source = self.source()
                prefix = "X-Synthetic-Padding: "
                source["response"]["raw_header_lines_bytes_utf8"].insert(
                    0, prefix + "A" * (size - len(prefix) - 2) + "\r\n"
                )
                code, output, _ = self.run_preview(source)
                self.assertEqual(code, 0 if accepted else 2)
                self.assertEqual(bool(output), accepted)

    def test_request_body_limit_rejects_before_canonical_decode_above_cap(self) -> None:
        for size, parser_called in ((8_191, True), (8_192, True), (8_193, False)):
            with self.subTest(request_body_bytes=size):
                source = self.source()
                source["request"]["body_bytes_utf8"] = "A" * size
                with patch.object(
                    adapter,
                    "identity_query_from_canonical_body",
                    side_effect=ValueError("synthetic input is invalid"),
                ) as parser:
                    with self.assertRaisesRegex(ValueError, "synthetic input is invalid"):
                        StructuralHardcoverIdentityPreview().run(source)
                self.assertEqual(parser.called, parser_called)

    def test_report_enforces_only_reachable_public_states(self) -> None:
        valid = (
            (FixedAdapterOutcome.MATCHED, 1, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MATCHED, 3, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.NO_MATCH, 0, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.AMBIGUOUS, 2, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.AMBIGUOUS, 4, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MALFORMED, 0, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MALFORMED, 4, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.INCOMPATIBLE, 4, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.INCOMPATIBLE, 0, False, False, AddressPolicyOutcome.REJECTED),
        )
        for values in valid:
            with self.subTest(valid=values):
                FixedAdapterPreviewReport(*values).validate()

        invalid = (
            (FixedAdapterOutcome.MATCHED, 0, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MATCHED, 4, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.NO_MATCH, 1, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.AMBIGUOUS, 1, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MALFORMED, 5, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MATCHED, 1, False, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MATCHED, 1, True, True, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.INCOMPATIBLE, 1, False, False, AddressPolicyOutcome.REJECTED),
            (FixedAdapterOutcome.NO_MATCH, 0, False, False, AddressPolicyOutcome.REJECTED),
            ("matched", 1, True, False, AddressPolicyOutcome.ACCEPTED),
            (FixedAdapterOutcome.MATCHED, True, True, False, AddressPolicyOutcome.ACCEPTED),
        )
        for values in invalid:
            with self.subTest(invalid=values):
                with self.assertRaises(TypeError):
                    FixedAdapterPreviewReport(*values).to_json()

    def test_self_referencing_alias_reports_validated_malformed_rows(self) -> None:
        source = self.source()
        document = json.loads(source["response"]["raw_body_bytes_utf8"])
        alias = document["data"]["editions"][1]
        alias["canonical_id"] = alias["id"]
        alias["book"]["canonical_id"] = alias["book"]["id"]
        body = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        source["response"]["raw_body_bytes_utf8"] = body
        source["response"]["raw_header_lines_bytes_utf8"][-1] = (
            f"Content-Length: {len(body.encode())}\r\n"
        )
        code, output, errors = self.run_preview(source)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(
            json.loads(output)["result"],
            {
                "actual_network": False,
                "address_policy": "accepted",
                "outcome": "malformed",
                "peer_equal": True,
                "row_count": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
