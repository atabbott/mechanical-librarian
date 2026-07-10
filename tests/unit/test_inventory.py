from __future__ import annotations

from datetime import UTC, datetime
import unittest

from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    ConnectionId,
    DomainValueError,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    SnapshotId,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    CoverReference,
    FileReference,
    FieldName,
    ItemLifecycle,
    ItemSnapshot,
    LibraryConnection,
    LibraryItem,
    MediaType,
    MetadataField,
    NormalizedMetadata,
    ObservationProvenance,
    RawObservation,
    SecretReference,
    ServiceEndpoint,
)


class InventoryEntityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = AdapterId("audiobookshelf")
        self.target = TargetId(
            adapter=self.adapter,
            connection=ConnectionId("primary"),
            library=LibraryId("books"),
            external_item=ExternalItemId("book-123"),
        )
        self.fingerprint = Fingerprint.from_canonical_value(
            {"title": "Example"}, schema=SchemaVersion("snapshot-v1")
        )

    def test_capability_declarations_reject_inconsistent_combinations(self) -> None:
        with self.assertRaises(DomainValueError):
            AdapterCapabilities(
                inventory_read=False,
                metadata_read=True,
                media_file_read=False,
                notifications=False,
                pagination=False,
                concurrency_tokens=False,
                cover_read=False,
                writable_fields=frozenset(),
                cover_write=False,
                supported_media_types=frozenset({MediaType.EBOOK}),
            )

    def test_connection_keeps_endpoint_and_secret_out_of_repr(self) -> None:
        connection = LibraryConnection(
            id=ConnectionId("primary"),
            adapter=self.adapter,
            endpoint=ServiceEndpoint("https://library.example"),
            secret=SecretReference("docker-secret/audiobookshelf"),
            capabilities=self._capabilities(),
        )

        self.assertNotIn("library.example", repr(connection))
        self.assertNotIn("docker-secret", repr(connection))

    def test_service_endpoint_rejects_unsafe_or_incomplete_origins(self) -> None:
        for value in ("https://", "ftp://library.example", "https://token@library.example"):
            with self.subTest(value=value):
                with self.assertRaises(DomainValueError):
                    ServiceEndpoint(value)

    def test_normalized_metadata_is_immutable_and_has_one_value_per_field(self) -> None:
        metadata = NormalizedMetadata(
            (MetadataField(FieldName.TITLE, ("Example",)),)
        )
        self.assertEqual(metadata.get(FieldName.TITLE), MetadataField(FieldName.TITLE, ("Example",)))

        with self.assertRaises(DomainValueError):
            NormalizedMetadata(
                (
                    MetadataField(FieldName.TITLE, ("One",)),
                    MetadataField(FieldName.TITLE, ("Two",)),
                )
            )

    def test_snapshot_keeps_raw_and_normalized_observations_separate(self) -> None:
        snapshot = ItemSnapshot(
            id=SnapshotId("snapshot-one"),
            target=self.target,
            fingerprint=self.fingerprint,
            metadata=NormalizedMetadata((MetadataField(FieldName.TITLE, ("Example",)),)),
            files=(FileReference("books", "fiction/example.epub"),),
            cover=CoverReference("private-cover"),
            provenance=ObservationProvenance(
                adapter_version=AdapterVersion("abs-v1"),
                observed_at=datetime(2026, 7, 11, tzinfo=UTC),
                raw_observation=RawObservation(self.adapter, self.fingerprint, "private-artifact"),
            ),
            service_version=AdapterVersion("abs-v1"),
        )

        self.assertNotIn("private-artifact", repr(snapshot.provenance.raw_observation))
        self.assertNotIn("fiction/example.epub", repr(snapshot.files[0]))

    def test_item_identity_does_not_contain_snapshot_state(self) -> None:
        item = LibraryItem(self.target, MediaType.EBOOK, ItemLifecycle.ACTIVE, (SnapshotId("snapshot-one"),))
        self.assertEqual(item.target, self.target)
        with self.assertRaises(DomainValueError):
            LibraryItem(
                self.target,
                MediaType.EBOOK,
                ItemLifecycle.ACTIVE,
                (SnapshotId("snapshot-one"), SnapshotId("snapshot-one")),
            )

    def _capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            inventory_read=True,
            metadata_read=True,
            media_file_read=True,
            notifications=True,
            pagination=True,
            concurrency_tokens=True,
            cover_read=True,
            writable_fields=frozenset({FieldName.TITLE}),
            cover_write=True,
            supported_media_types=frozenset({MediaType.EBOOK, MediaType.AUDIOBOOK}),
        )


if __name__ == "__main__":
    unittest.main()
