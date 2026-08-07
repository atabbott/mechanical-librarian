from __future__ import annotations

import unittest

from scripts.run_required_test_shard import leaf_tests, shard_for


class RequiredTestShardTests(unittest.TestCase):
    def test_partition_is_deterministic_disjoint_and_complete(self) -> None:
        identifiers = tuple(
            f"tests.synthetic.Case.test_{index}" for index in range(100)
        )
        partitions = tuple(
            {value for value in identifiers if shard_for(value, 3) == shard}
            for shard in range(3)
        )
        self.assertEqual(set().union(*partitions), set(identifiers))
        for index, partition in enumerate(partitions):
            for other in partitions[index + 1 :]:
                self.assertTrue(partition.isdisjoint(other))
        self.assertEqual(shard_for("tests.synthetic.Case.test_0", 3), 1)

    def test_leaf_tests_preserve_every_discovered_case(self) -> None:
        class SyntheticCase(unittest.TestCase):
            def test_one(self) -> None:
                pass

            def test_two(self) -> None:
                pass

        nested = unittest.TestSuite(
            (unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticCase),)
        )
        self.assertEqual(len(tuple(leaf_tests(nested))), 2)


if __name__ == "__main__":
    unittest.main()
