from __future__ import annotations

import argparse
import importlib.util
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "connector_state.py"
SPEC = importlib.util.spec_from_file_location("connector_state", SCRIPT)
assert SPEC and SPEC.loader
connector_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connector_state)

OPENING = "a" * 40
REQUESTED = "b" * 40
CURRENT = "c" * 40
REQUEST_TIME = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
REQUESTED_AT = "2026-07-15T10:00:00Z"
ACTOR = {"login": "chatgpt-codex-connector[bot]"}


def args(*, c2: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        repo="owner/repo",
        pr=7,
        requested_at=REQUESTED_AT,
        opening_head=OPENING,
        trigger_comment=91 if c2 else None,
        requested_head=REQUESTED if c2 else None,
    )


def reaction(content: str, at: str, event_id: int = 1) -> dict:
    return {"id": event_id, "user": ACTOR, "content": content, "created_at": at}


class ConnectorStateTests(unittest.TestCase):
    def collect(self, *, c2: bool = False, lists: dict[str, list[dict]] | None = None, observed_seconds: int = 30):
        lists = lists or {}
        pr = {"created_at": REQUESTED_AT, "head": {"sha": CURRENT if c2 else OPENING}}
        trigger = {
            "id": 91,
            "issue_url": "https://api.github.test/repos/owner/repo/issues/7",
            "created_at": REQUESTED_AT,
            "body": "@codex review",
            "user": {"login": "maintainer"},
        }

        def object_reader(endpoint: str):
            return trigger if endpoint.endswith("issues/comments/91") else pr

        with patch.object(connector_state, "gh_object", side_effect=object_reader), patch.object(
            connector_state, "gh_items", side_effect=lambda endpoint: lists.get(endpoint, [])
        ):
            return connector_state.collect_report(
                args(c2=c2), observed_at=REQUEST_TIME + timedelta(seconds=observed_seconds)
            )

    def test_paginated_reader_uses_all_pages(self) -> None:
        completed = subprocess.CompletedProcess([], 0, '[[{"id": 1}], [{"id": 2}]]', "")
        with patch.object(connector_state.subprocess, "run", return_value=completed) as run:
            items = connector_state.gh_items("repos/owner/repo/pulls/7/reviews")
        self.assertEqual([1, 2], [item["id"] for item in items])
        command = run.call_args.args[0]
        self.assertIn("--paginate", command)
        self.assertIn("--slurp", command)

    def test_pickup_unobserved_is_replaced_by_late_clean_result(self) -> None:
        self.assertEqual("PENDING_PICKUP", self.collect(observed_seconds=119)["state"])
        self.assertEqual("PICKUP_UNOBSERVED", self.collect(observed_seconds=120)["state"])
        self.assertEqual("PICKUP_UNOBSERVED", self.collect(observed_seconds=121)["state"])
        endpoint = "repos/owner/repo/issues/7/reactions"
        late = self.collect(
            lists={endpoint: [reaction("+1", "2026-07-15T10:03:00Z", 22)]},
            observed_seconds=240,
        )
        self.assertEqual("CLEAN", late["state"])
        self.assertEqual(180.0, late["latency_seconds"]["terminal"])
        self.assertEqual([22], late["ids"]["reaction_ids"])

    def test_plus_one_is_not_clean_while_eyes_remains(self) -> None:
        endpoint = "repos/owner/repo/issues/7/reactions"
        report = self.collect(
            lists={
                endpoint: [
                    reaction("eyes", "2026-07-15T10:00:05Z", 1),
                    reaction("+1", "2026-07-15T10:00:20Z", 2),
                ]
            }
        )
        self.assertEqual("INCONSISTENT", report["state"])
        self.assertIn("EYES_AND_PLUS_ONE_CONFLICT", report["protocol_variances"])

    def test_c2_off_target_terminal_reaction_does_not_become_clean(self) -> None:
        report = self.collect(
            c2=True,
            lists={
                "repos/owner/repo/issues/7/reactions": [
                    reaction("+1", "2026-07-15T10:00:20Z", 19)
                ]
            },
        )
        self.assertEqual("TERMINAL_OFF_TARGET", report["state"])
        self.assertEqual(20.0, report["latency_seconds"]["off_target_terminal"])
        self.assertEqual("TRIGGER_COMMENT", report["request"]["target_kind"])

    def test_review_on_wrong_commit_is_terminal_off_target(self) -> None:
        review = {
            "id": 33,
            "user": ACTOR,
            "submitted_at": "2026-07-15T10:00:40Z",
            "commit_id": "d" * 40,
            "body": "Finding",
            "state": "COMMENTED",
        }
        report = self.collect(
            c2=True,
            lists={"repos/owner/repo/pulls/7/reviews": [review]},
        )
        self.assertEqual("TERMINAL_OFF_TARGET", report["state"])
        self.assertEqual("d" * 40, report["events"][0]["commit_id"])
        self.assertEqual([33], report["ids"]["review_ids"])

    def test_separate_result_comment_completes_findings(self) -> None:
        comment = {
            "id": 44,
            "user": ACTOR,
            "created_at": "2026-07-15T10:00:25Z",
            "body": f"Result for {OPENING}: one finding",
        }
        report = self.collect(lists={"repos/owner/repo/issues/7/comments": [comment]})
        self.assertEqual("FINDINGS_POSTED", report["state"])
        self.assertEqual(25.0, report["latency_seconds"]["terminal"])
        self.assertEqual([44], report["ids"]["result_comment_ids"])

    def test_trigger_syntax_and_exact_timestamp_fail_closed(self) -> None:
        self.assertEqual("@codex review\nFocus: parser", connector_state.normalize_trigger(" @codex review \r\n Focus: parser "))
        for body in ("please @codex review", "@codex review parser", "@Codex review"):
            with self.subTest(body=body), self.assertRaises(connector_state.ProtocolInputError):
                connector_state.normalize_trigger(body)

        bad = args()
        bad.requested_at = "2026-07-15T10:00:01Z"
        with patch.object(
            connector_state, "gh_object", return_value={"created_at": REQUESTED_AT, "head": {"sha": OPENING}}
        ), patch.object(connector_state, "gh_items", return_value=[]):
            with self.assertRaisesRegex(connector_state.ProtocolInputError, "exactly equal"):
                connector_state.collect_report(bad, observed_at=REQUEST_TIME + timedelta(seconds=3))

    def test_c2_requires_explicit_requested_head(self) -> None:
        bad = args(c2=True)
        bad.requested_head = None
        with self.assertRaisesRegex(connector_state.ProtocolInputError, "requested-head"):
            connector_state.collect_report(bad, observed_at=REQUEST_TIME + timedelta(seconds=3))


if __name__ == "__main__":
    unittest.main()
