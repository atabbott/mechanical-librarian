#!/usr/bin/env python3
"""Run one deterministic, disjoint shard of the required public test suite."""

from __future__ import annotations

import argparse
import hashlib
import unittest
from collections.abc import Iterator
from pathlib import Path


def shard_for(test_id: str, shard_count: int) -> int:
    digest = hashlib.sha256(test_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def leaf_tests(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for value in suite:
        if isinstance(value, unittest.TestSuite):
            yield from leaf_tests(value)
        else:
            yield value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.shard_count <= 16 or not (
        0 <= arguments.shard_index < arguments.shard_count
    ):
        parser.error("invalid shard")

    root = Path(__file__).resolve().parents[1]
    discovered = unittest.defaultTestLoader.discover(
        str(root / "tests"),
        pattern="test_*.py",
        top_level_dir=str(root),
    )
    selected = unittest.TestSuite(
        test
        for test in leaf_tests(discovered)
        if shard_for(test.id(), arguments.shard_count) == arguments.shard_index
    )
    result = unittest.TextTestRunner(verbosity=1).run(selected)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
