from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.inventory import SQLiteInventoryRepository
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate
from tests.contract.service_ports import ServicePortContract, standard_family_contracts


class SQLiteInventoryRepositoryContractTests(unittest.TestCase):
    def test_conforms_to_snapshot_repository_family(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            contract = next(
                family for family in standard_family_contracts() if family.name == "repository"
            )

            ServicePortContract(self).require_family(
                SQLiteInventoryRepository(connection), contract
            )


if __name__ == "__main__":
    unittest.main()
