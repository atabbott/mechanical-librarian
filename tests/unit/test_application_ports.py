from __future__ import annotations

import unittest

from mechanical_librarian.application.ports import FailureKind, PageRequest, PortFailure, PortResult


class PortContractTests(unittest.TestCase):
    def test_result_requires_exactly_one_outcome(self) -> None:
        with self.assertRaises(ValueError):
            PortResult()
        with self.assertRaises(ValueError):
            PortResult(value="value", failure=PortFailure(FailureKind.TIMEOUT, True, "timed out"))
        self.assertTrue(PortResult(value="value").value)

    def test_page_limits_are_bounded(self) -> None:
        for limit in (0, 501):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    PageRequest(None, limit)


if __name__ == "__main__":
    unittest.main()
