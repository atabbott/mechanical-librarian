from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import review_packet  # noqa: E402


class ReviewPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name).resolve()
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Test")
        (self.repo / "requirement.md").write_text("requirement\n", encoding="utf-8")
        (self.repo / "work.txt").write_text("before\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.repo / "work.txt").write_text("after\n", encoding="utf-8")
        self.git("add", "work.txt")
        self.packet = self.repo / "packet.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments], cwd=self.repo, text=True, capture_output=True, check=True
        )
        return result.stdout

    def create(self, *, mode: str = "STAGED", target: str | None = None) -> str:
        args = argparse.Namespace(
            repo=self.repo,
            mode=mode,
            base=self.base,
            target=target,
            workflow="deliver-focused-change",
            task="acceptance and code quality",
            lens="false completion and unnecessary complexity",
            requirement=["requirement.md"],
            output=self.packet,
        )
        _, value = review_packet.create(args)
        return value

    def test_staged_packet_round_trips_against_live_git(self) -> None:
        value = self.create()
        packet, observed = review_packet.verify(self.packet, self.repo, value)
        self.assertEqual(value, observed)
        self.assertEqual(["work.txt"], packet["repository"]["changed_paths"])
        self.assertEqual("requirement\n", packet["requirements"][0]["content"])

    def test_self_consistent_forged_diff_is_rejected_by_live_git(self) -> None:
        self.create()
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        packet["repository"]["diff"] = "forged\n"
        packet["repository"]["diff_sha256"] = review_packet.digest(b"forged\n")
        raw = review_packet.canonical(packet)
        self.packet.write_bytes(raw)
        with self.assertRaisesRegex(review_packet.PacketError, "repository evidence drifted"):
            review_packet.verify(self.packet, self.repo, review_packet.digest(raw))

    def test_staged_packet_rejects_index_drift(self) -> None:
        value = self.create()
        (self.repo / "work.txt").write_text("changed again\n", encoding="utf-8")
        self.git("add", "work.txt")
        with self.assertRaisesRegex(review_packet.PacketError, "repository evidence drifted"):
            review_packet.verify(self.packet, self.repo, value)

    def test_requirement_drift_is_rejected(self) -> None:
        value = self.create()
        (self.repo / "requirement.md").write_text("changed requirement\n", encoding="utf-8")
        with self.assertRaisesRegex(review_packet.PacketError, "requirement drifted"):
            review_packet.verify(self.packet, self.repo, value)

    def test_commit_packet_round_trips(self) -> None:
        self.git("commit", "-m", "target")
        target = self.git("rev-parse", "HEAD").strip()
        value = self.create(mode="COMMIT", target=target)
        packet, _ = review_packet.verify(self.packet, self.repo, value)
        self.assertEqual(target, packet["repository"]["target"])

    def test_unsafe_requirement_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(review_packet.PacketError, "unsafe requirement path"):
            review_packet.safe_path(self.repo, "../outside")

    def test_malformed_requirement_fields_are_rejected_cleanly(self) -> None:
        self.create()
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        packet["requirements"][0]["content"] = None
        with self.assertRaisesRegex(review_packet.PacketError, "requirement fields"):
            review_packet.validate_shape(packet)


if __name__ == "__main__":
    unittest.main()
