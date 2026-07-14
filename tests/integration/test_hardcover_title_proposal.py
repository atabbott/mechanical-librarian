from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx

from mechanical_librarian.bootstrap.composition import (
    _run_hardcover_title_proposal_report,
    _run_synthetic_title_proposal_report,
)
from mechanical_librarian.interfaces.cli import main as cli_main


PUBLIC_IP = "93.184.216.34"
TITLE = "A Synthetic Book"


class _Peer:
    def get_extra_info(self, name: str):
        return (PUBLIC_IP, 443) if name == "server_addr" else None


def _payload(results: list[object]) -> dict[str, object]:
    return {
        "data": {
            "search": {
                "error": None,
                "page": 1,
                "per_page": 1,
                "query": TITLE,
                "query_type": "Book",
                "results": results,
            }
        }
    }


def _transport(payload: object | None = None, *, status: int = 200, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        body = json.dumps({} if payload is None else payload, separators=(",", ":"))
        return httpx.Response(
            status,
            headers={"content-type": "application/json"},
            content=body.encode("utf-8"),
            extensions={"network_stream": _Peer()},
        )

    return httpx.MockTransport(handler)


def _secret(root: Path, value: str = "provider-token-sentinel") -> Path:
    secret = root / "provider-token"
    secret.write_text(value + "\n", encoding="utf-8")
    secret.chmod(0o600)
    return secret


class HardcoverTitleProposalIntegrationTests(unittest.TestCase):
    def test_disabled_path_constructs_no_provider_or_secret_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            with (
                patch(
                    "mechanical_librarian.bootstrap.composition.PrivateFileProviderCredentials"
                ) as credentials,
                patch(
                    "mechanical_librarian.bootstrap.composition.HardcoverTitleProvider"
                ) as provider,
            ):
                report = _run_hardcover_title_proposal_report(database, None)
            credentials.assert_not_called()
            provider.assert_not_called()
            self.assertEqual(report.proposal_set.result.value, "provider_disabled")
            self.assertEqual(report.proposal_set.proposals, ())
            self.assertTrue(report.proposal_set.lineage.startswith("hardcover-poc-"))
            self.assertFalse(report.proposal_set.production_applicable)

    def test_supported_fake_candidate_uses_real_command_composition_and_persists_minimally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite"
            secret = _secret(root)
            calls = []
            report = _run_hardcover_title_proposal_report(
                database,
                secret,
                transport=_transport(_payload([{"title": TITLE}]), calls=calls),
                resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
            )
            self.assertEqual(report.proposal_set.result.value, "needs_review")
            self.assertEqual(report.proposal_set.proposals[0].proposed_value, TITLE)
            synthetic = _run_synthetic_title_proposal_report(
                root / "synthetic.sqlite", "agree-empty"
            )
            self.assertEqual(
                report.proposal_set.proposals[0].id,
                synthetic.proposal_set.proposals[0].id,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].headers["authorization"], "provider-token-sentinel")
            persisted = database.read_bytes()
            for private in (
                b"provider-token-sentinel",
                str(secret).encode("utf-8"),
                b"query HardcoverTitlePoc",
            ):
                self.assertNotIn(private, persisted)

    def test_missing_secret_and_provider_failures_preserve_local_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_calls = []
            missing = _run_hardcover_title_proposal_report(
                root / "missing.sqlite",
                root / "missing-token",
                transport=_transport(_payload([]), calls=missing_calls),
                resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
            )
            self.assertEqual(missing.proposal_set.result.value, "provider_failed")
            self.assertEqual(missing_calls, [])
            chosen = missing.proposal_set.to_data()["manifest"]["f06"]["chosen_observation"]
            self.assertEqual(chosen["normalized_value"], TITLE)

            secret = _secret(root)
            failed = _run_hardcover_title_proposal_report(
                root / "failed.sqlite",
                secret,
                transport=_transport(status=401),
                resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
            )
            self.assertEqual(failed.proposal_set.result.value, "provider_failed")
            self.assertEqual(
                failed.proposal_set.to_data()["manifest"]["provider"]["failure_kind"],
                "authentication",
            )
            self.assertEqual(
                failed.proposal_set.to_data()["manifest"]["f06"]["chosen_observation"]["normalized_value"],
                TITLE,
            )

    def test_observation_lineages_replay_and_coexist_without_supersession(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite"
            secret = _secret(root)
            cases = (
                (_payload([{"title": TITLE}]), 200, "needs_review"),
                (_payload([{"title": "A Different Provider Title"}]), 200, "local_provider_conflict"),
                (_payload([]), 200, "provider_empty"),
                (None, 404, "provider_no_match"),
                (None, 401, "provider_failed"),
                (None, 408, "provider_failed"),
            )
            reports = []
            for payload, status, expected in cases:
                report = _run_hardcover_title_proposal_report(
                    database,
                    secret,
                    transport=_transport(payload, status=status),
                    resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
                )
                self.assertEqual(report.proposal_set.result.value, expected)
                self.assertEqual(report.proposal_set.lineage_revision, 1)
                self.assertIsNone(report.proposal_set.previous_set_id)
                reports.append(report)
            replay = _run_hardcover_title_proposal_report(
                database,
                secret,
                transport=_transport(_payload([{"title": TITLE}])),
                resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
            )
            self.assertEqual(replay.proposal_set, reports[0].proposal_set)
            self.assertTrue(replay.persistence.replayed)
            self.assertEqual(len({item.proposal_set.lineage for item in reports}), 6)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM synthetic_title_proposal_sets"
                    ).fetchone(),
                    (6,),
                )
            finally:
                connection.close()

    def test_existing_f09a_state_and_canonical_bytes_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite"
            original = _run_synthetic_title_proposal_report(database, "agree-empty")
            connection = sqlite3.connect(database)
            try:
                before = connection.execute(
                    "SELECT canonical_set FROM synthetic_title_proposal_sets WHERE set_id = ?",
                    (original.proposal_set.set_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            _run_hardcover_title_proposal_report(database, None)
            replay = _run_synthetic_title_proposal_report(database, "agree-empty")
            connection = sqlite3.connect(database)
            try:
                after = connection.execute(
                    "SELECT canonical_set FROM synthetic_title_proposal_sets WHERE set_id = ?",
                    (original.proposal_set.set_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(before, after)
            self.assertEqual(replay.proposal_set, original.proposal_set)
            self.assertTrue(replay.persistence.replayed)

    def test_malformed_adapter_output_publishes_no_new_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite"
            secret = _secret(root)
            with (
                patch(
                    "mechanical_librarian.adapters.providers.hardcover_title.HardcoverTitleProvider.lookup",
                    return_value=object(),
                ),
                self.assertRaises(Exception),
            ):
                _run_hardcover_title_proposal_report(
                    database,
                    secret,
                    transport=_transport(_payload([])),
                    resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
                )
            self.assertFalse(database.exists())

    def test_supported_command_uses_production_composition_fake_seam_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite"
            secret = _secret(root)
            calls = []

            def runner(selected_database: Path, selected_secret: Path | None):
                return _run_hardcover_title_proposal_report(
                    selected_database,
                    selected_secret,
                    transport=_transport(_payload([{"title": TITLE}]), calls=calls),
                    resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
                )

            def invoke(selected_database: Path, selected_secret: Path | None):
                output = StringIO()
                errors = StringIO()
                arguments = [
                    "hardcover-title-proposal-report",
                    "--database",
                    str(selected_database),
                    "--format",
                    "json",
                ]
                if selected_secret is not None:
                    arguments.extend(("--secret-reference", str(selected_secret)))
                status = cli_main(
                    arguments,
                    state_check_runner=lambda _database: None,  # type: ignore[arg-type]
                    hardcover_title_proposal_runner=runner,
                    stdout=output,
                    stderr=errors,
                )
                return status, output.getvalue(), errors.getvalue()

            first = invoke(database, secret)
            replay = invoke(database, secret)
            self.assertEqual(first[0], 0)
            self.assertEqual(replay[0], 0)
            self.assertEqual(first[2], "")
            self.assertEqual(replay[2], "")
            self.assertEqual(json.loads(first[1])["result"], "needs_review")
            self.assertFalse(json.loads(first[1])["persistence"]["replayed"])
            self.assertTrue(json.loads(replay[1])["persistence"]["replayed"])
            self.assertEqual(len(calls), 2)

            disabled = invoke(root / "disabled.sqlite", None)
            self.assertEqual(disabled[0], 0)
            self.assertEqual(json.loads(disabled[1])["result"], "provider_disabled")
            self.assertEqual(disabled[2], "")
            self.assertEqual(len(calls), 2)

            failed = invoke(root / "failed.sqlite", root / "missing-token")
            self.assertEqual(failed[0], 0)
            self.assertEqual(json.loads(failed[1])["result"], "provider_failed")
            self.assertEqual(failed[2], "")
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
