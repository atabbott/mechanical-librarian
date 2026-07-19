from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
