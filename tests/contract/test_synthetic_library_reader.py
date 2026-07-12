from __future__ import annotations

import unittest

from mechanical_librarian.adapters.library_services.synthetic import (
    SyntheticLibraryReader,
    synthetic_capabilities,
)
from mechanical_librarian.domain.identifiers import AdapterId, ConnectionId, LibraryId
from mechanical_librarian.domain.inventory import (
    LibraryConnection,
    SecretReference,
    ServiceEndpoint,
)
from tests.contract.service_ports import ServicePortContract, standard_family_contracts


class SyntheticLibraryReaderContractTests(unittest.TestCase):
    def test_conforms_to_read_only_library_reader_family(self) -> None:
        connection = LibraryConnection(
            id=ConnectionId("synthetic-connection"),
            adapter=AdapterId("synthetic"),
            endpoint=ServiceEndpoint("https://synthetic.invalid"),
            secret=SecretReference("synthetic-secret-reference"),
            capabilities=synthetic_capabilities(),
        )
        reader = SyntheticLibraryReader(connection, LibraryId("synthetic-library"))
        contract = next(
            family for family in standard_family_contracts() if family.name == "library-reader"
        )

        ServicePortContract(self).require_family(reader, contract)


if __name__ == "__main__":
    unittest.main()
