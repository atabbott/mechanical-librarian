from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
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
    ServiceVersion,
    SnapshotId,
    TargetId,
    canonical_json,
)
from mechanical_librarian.domain.inventory import (
    AdapterRelease,
    AdapterCapabilities,
    CanonicalSnapshotInput,
    ConcurrencyToken,
    CoverReference,
    FileReference,
    FieldName,
    ItemLifecycle,
    ItemSnapshot,
    InventoryRecord,
    LibraryConnection,
    LibraryItem,
    MediaType,
    MetadataField,
    NormalizedMetadata,
    ObservationProvenance,
    RawObservation,
    SecretReference,
    ServiceRelease,
    ServiceEndpoint,
    SNAPSHOT_CANONICAL_MAX_BYTES,
    SNAPSHOT_CANONICAL_SOURCE_MAX_BYTES,
    SNAPSHOT_SCHEMA_V1,
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
        self.assertEqual(
            metadata.get(FieldName.TITLE), MetadataField(FieldName.TITLE, ("Example",))
        )

        with self.assertRaises(DomainValueError):
            NormalizedMetadata(
                (
                    MetadataField(FieldName.TITLE, ("One",)),
                    MetadataField(FieldName.TITLE, ("Two",)),
                )
            )

    def test_snapshot_keeps_raw_and_normalized_observations_separate(self) -> None:
        snapshot = self._snapshot()

        self.assertNotIn("private-artifact", repr(snapshot.provenance.raw_observation))
        self.assertNotIn("fiction/example.epub", repr(snapshot.files[0]))
        self.assertNotIn("Example", repr(snapshot))
        self.assertNotIn("private-cover", repr(snapshot))

    def test_snapshot_rejects_adapter_and_capability_mismatches(self) -> None:
        with self.assertRaisesRegex(DomainValueError, "observation adapter scope"):
            ObservationProvenance(
                adapter_release=AdapterRelease(self.adapter, AdapterVersion("adapter-v1")),
                observed_at=datetime(2026, 7, 11, tzinfo=UTC),
                raw_observation=RawObservation(
                    AdapterId("other"), self.fingerprint, "private-artifact"
                ),
            )

        provenance = self._provenance(adapter=AdapterId("other"))
        with self.assertRaisesRegex(DomainValueError, "adapter scope"):
            self._snapshot(provenance=provenance)

        snapshot = self._snapshot()
        with self.assertRaisesRegex(DomainValueError, "adapter scope"):
            replace(
                snapshot,
                service_release=ServiceRelease(
                    AdapterId("other"), ServiceVersion("service-v1")
                ),
            )

        ebook_capabilities = replace(
            self._capabilities(), supported_media_types=frozenset({MediaType.EBOOK})
        )
        with self.assertRaisesRegex(DomainValueError, "unsupported"):
            self._snapshot(media_type=MediaType.AUDIOBOOK, capabilities=ebook_capabilities)

        without_tokens = replace(self._capabilities(), concurrency_tokens=False)
        with self.assertRaisesRegex(DomainValueError, "concurrency evidence"):
            self._snapshot(capabilities=without_tokens)

    def test_release_constructors_reject_swapped_version_kinds(self) -> None:
        with self.assertRaisesRegex(DomainValueError, "adapter version"):
            AdapterRelease(self.adapter, ServiceVersion("service-v1"))  # type: ignore[arg-type]
        with self.assertRaisesRegex(DomainValueError, "service version"):
            ServiceRelease(self.adapter, AdapterVersion("adapter-v1"))  # type: ignore[arg-type]

    def test_inventory_collections_must_be_immutable(self) -> None:
        with self.assertRaisesRegex(DomainValueError, "immutable values"):
            MetadataField(FieldName.TITLE, ["Example"])  # type: ignore[arg-type]
        with self.assertRaisesRegex(DomainValueError, "immutable tuple"):
            NormalizedMetadata([])  # type: ignore[arg-type]
        with self.assertRaisesRegex(DomainValueError, "immutable field-name set"):
            replace(
                self._capabilities(), writable_fields={FieldName.TITLE}
            )  # type: ignore[arg-type]
        with self.assertRaisesRegex(DomainValueError, "immutable media-type set"):
            replace(
                self._capabilities(), supported_media_types={MediaType.EBOOK}
            )  # type: ignore[arg-type]
        with self.assertRaisesRegex(DomainValueError, "immutable tuple"):
            LibraryItem(
                self.target,
                MediaType.EBOOK,
                ItemLifecycle.ACTIVE,
                [SnapshotId("snapshot-one")],  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(DomainValueError, "immutable tuple"):
            replace(self._snapshot(), files=[])  # type: ignore[arg-type]

    def test_snapshot_rejects_canonical_state_or_fingerprint_mismatch(self) -> None:
        snapshot = self._snapshot()
        changed_metadata = NormalizedMetadata((MetadataField(FieldName.TITLE, ("Changed",)),))
        with self.assertRaisesRegex(DomainValueError, "canonical input"):
            replace(snapshot, metadata=changed_metadata)

        unrelated = Fingerprint.from_canonical_value(
            {"unrelated": True}, schema=SNAPSHOT_SCHEMA_V1
        )
        with self.assertRaisesRegex(DomainValueError, "fingerprint"):
            replace(snapshot, fingerprint=unrelated)

        with self.assertRaisesRegex(DomainValueError, "unsupported canonical snapshot schema"):
            CanonicalSnapshotInput(
                SchemaVersion("snapshot-v2"), snapshot.canonical_input.serialized
            )
        with self.assertRaisesRegex(DomainValueError, "schema marker"):
            CanonicalSnapshotInput.from_json("{}")
        with self.assertRaisesRegex(DomainValueError, "fields are invalid"):
            CanonicalSnapshotInput.from_json('{"extra":true,"schema":"snapshot-v1"}')
        with self.assertRaisesRegex(DomainValueError, "size limit"):
            CanonicalSnapshotInput.from_json("x" * (SNAPSHOT_CANONICAL_MAX_BYTES + 1))
        with self.assertRaises(DomainValueError):
            CanonicalSnapshotInput.from_json("[" * 2_000 + "]" * 2_000)
        oversized_integer = '{"schema":"snapshot-v1","x":' + "1" * 5_000 + "}"
        with self.assertRaisesRegex(DomainValueError, "valid JSON"):
            CanonicalSnapshotInput.from_json(oversized_integer)

    def test_canonical_reader_rejects_invalid_cross_field_state(self) -> None:
        serialized = self._snapshot().canonical_input.serialized
        mutations = {}

        adapter_mismatch = json.loads(serialized)
        adapter_mismatch["service_release"]["adapter"] = "other"
        mutations["adapter"] = adapter_mismatch

        concurrency_mismatch = json.loads(serialized)
        concurrency_mismatch["concurrency_identity"] = None
        mutations["concurrency"] = concurrency_mismatch

        media_mismatch = json.loads(serialized)
        media_mismatch["capabilities"]["supported_media_types"] = ["audiobook"]
        mutations["media"] = media_mismatch

        unnormalized_metadata = json.loads(serialized)
        unnormalized_metadata["metadata"][0]["values"] = [" Example "]
        mutations["metadata"] = unnormalized_metadata

        for name, value in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(DomainValueError):
                    CanonicalSnapshotInput.from_json(canonical_json(value))

    def test_canonical_generation_rejects_oversized_source_before_serialization(self) -> None:
        metadata = NormalizedMetadata(
            (
                MetadataField(
                    FieldName.TITLE,
                    ("x" * (SNAPSHOT_CANONICAL_SOURCE_MAX_BYTES + 1),),
                ),
            )
        )
        with self.assertRaisesRegex(DomainValueError, "source exceeds"):
            self._snapshot(metadata=metadata)

    def test_canonical_reader_and_writer_reject_unpaired_unicode_surrogates(self) -> None:
        metadata = NormalizedMetadata(
            (MetadataField(FieldName.TITLE, ("invalid-\ud800",)),)
        )
        with self.assertRaisesRegex(DomainValueError, "invalid Unicode"):
            self._snapshot(metadata=metadata)

        stored = json.loads(self._snapshot().canonical_input.serialized)
        stored["metadata"][0]["values"] = ["invalid-\ud800"]
        with self.assertRaisesRegex(DomainValueError, "metadata field values"):
            CanonicalSnapshotInput.from_json(canonical_json(stored))

        actual_surrogate = self._snapshot().canonical_input.serialized.replace(
            '"Example"', '"invalid-\ud800"'
        )
        with self.assertRaisesRegex(DomainValueError, "invalid Unicode"):
            CanonicalSnapshotInput.from_json(actual_surrogate)

    def test_inventory_record_rejects_cross_scope_combinations(self) -> None:
        snapshot = self._snapshot()
        item = LibraryItem(
            self.target,
            MediaType.EBOOK,
            ItemLifecycle.ACTIVE,
            (snapshot.id,),
        )
        connection = self._connection()
        self.assertEqual(InventoryRecord(connection, item, snapshot).snapshot, snapshot)

        mismatch_cases = {
            "adapter": replace(connection, adapter=AdapterId("other")),
            "connection": replace(connection, id=ConnectionId("secondary")),
        }
        for name, mismatched_connection in mismatch_cases.items():
            with self.subTest(name=name):
                with self.assertRaises(DomainValueError):
                    InventoryRecord(mismatched_connection, item, snapshot)

        target_mismatches = {
            "library": replace(self.target, library=LibraryId("other")),
            "external_item": replace(
                self.target, external_item=ExternalItemId("other-item")
            ),
        }
        for name, target in target_mismatches.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(DomainValueError, "target scope"):
                    InventoryRecord(connection, replace(item, target=target), snapshot)

        with self.assertRaisesRegex(DomainValueError, "does not reference"):
            InventoryRecord(connection, replace(item, snapshot_ids=()), snapshot)
        with self.assertRaisesRegex(DomainValueError, "media type"):
            InventoryRecord(connection, replace(item, media_type=MediaType.AUDIOBOOK), snapshot)
        with self.assertRaisesRegex(DomainValueError, "lifecycle"):
            InventoryRecord(connection, replace(item, lifecycle=ItemLifecycle.REMOVED), snapshot)
        with self.assertRaisesRegex(DomainValueError, "capability"):
            InventoryRecord(
                replace(
                    connection,
                    capabilities=replace(self._capabilities(), notifications=False),
                ),
                item,
                snapshot,
            )

    def test_canonical_input_is_order_stable_and_round_trips(self) -> None:
        metadata = NormalizedMetadata(
            (
                MetadataField(FieldName.AUTHORS, ("A. Author",)),
                MetadataField(FieldName.TITLE, ("Example",)),
            )
        )
        files = (
            FileReference("books", "second.epub"),
            FileReference("books", "first.epub"),
        )
        first = self._snapshot(metadata=metadata, files=files)
        second = self._snapshot(
            metadata=NormalizedMetadata(tuple(reversed(metadata.fields))),
            files=tuple(reversed(files)),
            observed_at=datetime(2026, 7, 12, tzinfo=UTC),
            id=SnapshotId("snapshot-two"),
        )

        self.assertTrue(first.same_canonical_state(second))
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            CanonicalSnapshotInput.from_json(first.canonical_input.serialized),
            first.canonical_input,
        )
        parsed = json.loads(first.canonical_input.serialized)
        self.assertEqual(parsed["schema"], "snapshot-v1")
        self.assertNotIn("observed_at", parsed)
        self.assertNotIn("snapshot-one", first.canonical_input.serialized)
        self.assertNotIn("first.epub", first.canonical_input.serialized)
        self.assertNotIn("token-value", first.canonical_input.serialized)

    def test_every_canonical_state_change_changes_equality_and_fingerprint(self) -> None:
        baseline = self._snapshot()
        variants = {
            "target": self._snapshot(
                target=replace(self.target, library=LibraryId("other-library"))
            ),
            "media": self._snapshot(media_type=MediaType.AUDIOBOOK),
            "lifecycle": self._snapshot(lifecycle=ItemLifecycle.UNAVAILABLE),
            "metadata": self._snapshot(
                metadata=NormalizedMetadata(
                    (MetadataField(FieldName.TITLE, ("Changed",)),)
                )
            ),
            "files": self._snapshot(files=(FileReference("books", "other.epub"),)),
            "cover": self._snapshot(cover=CoverReference("other-cover", self.fingerprint)),
            "adapter_release": self._snapshot(
                provenance=self._provenance(adapter_version=AdapterVersion("adapter-v2"))
            ),
            "service_release": self._snapshot(
                service_release=ServiceRelease(
                    self.adapter, ServiceVersion("service-v2")
                )
            ),
            "raw_observation": self._snapshot(
                provenance=self._provenance(raw_value="changed")
            ),
            "concurrency": self._snapshot(
                concurrency_token=ConcurrencyToken("other-token")
            ),
            "capabilities": self._snapshot(
                capabilities=replace(self._capabilities(), notifications=False)
            ),
        }
        for name, variant in variants.items():
            with self.subTest(name=name):
                self.assertFalse(baseline.same_canonical_state(variant))
                self.assertNotEqual(baseline.fingerprint, variant.fingerprint)

    def test_canonical_v1_compatibility_fixture_is_stable(self) -> None:
        snapshot = self._snapshot()
        self.assertEqual(snapshot.canonical_input.schema, SchemaVersion("snapshot-v1"))
        self.assertEqual(snapshot.fingerprint.schema, SchemaVersion("snapshot-v1"))
        self.assertEqual(
            snapshot.fingerprint.digest,
            "de57e25adf0a09aa17a48c7c66f0fdf2a5977e67f7942cc65570adda858cb8d7",
        )

    def test_canonical_v1_is_stable_across_process_locale_and_timezone(self) -> None:
        snapshot = self._snapshot()
        script = (
            "import sys; "
            "from mechanical_librarian.domain.inventory import CanonicalSnapshotInput; "
            "print(CanonicalSnapshotInput.from_json(sys.stdin.read()).fingerprint().digest)"
        )
        source = str(Path(__file__).parents[2] / "src")
        for locale_name, timezone in (("C", "UTC"), ("C.UTF-8", "Pacific/Auckland")):
            with self.subTest(locale=locale_name, timezone=timezone):
                environment = os.environ | {
                    "LC_ALL": locale_name,
                    "PYTHONPATH": source,
                    "TZ": timezone,
                }
                completed = subprocess.run(
                    [sys.executable, "-c", script],
                    check=True,
                    capture_output=True,
                    env=environment,
                    input=snapshot.canonical_input.serialized,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(completed.stdout.strip(), snapshot.fingerprint.digest)

    def test_item_identity_does_not_contain_snapshot_state(self) -> None:
        item = LibraryItem(
            self.target,
            MediaType.EBOOK,
            ItemLifecycle.ACTIVE,
            (SnapshotId("snapshot-one"),),
        )
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

    def _connection(self) -> LibraryConnection:
        return LibraryConnection(
            id=ConnectionId("primary"),
            adapter=self.adapter,
            endpoint=ServiceEndpoint("https://library.example"),
            secret=SecretReference("docker-secret/audiobookshelf"),
            capabilities=self._capabilities(),
        )

    def _provenance(
        self,
        *,
        adapter: AdapterId | None = None,
        adapter_version: AdapterVersion | None = None,
        observed_at: datetime | None = None,
        raw_value: str = "observation",
    ) -> ObservationProvenance:
        selected_adapter = adapter or self.adapter
        return ObservationProvenance(
            adapter_release=AdapterRelease(
                selected_adapter, adapter_version or AdapterVersion("adapter-v1")
            ),
            observed_at=observed_at or datetime(2026, 7, 11, tzinfo=UTC),
            raw_observation=RawObservation(
                selected_adapter,
                Fingerprint.from_canonical_value(
                    {"raw": raw_value}, schema=SchemaVersion("raw-v1")
                ),
                "private-artifact",
            ),
        )

    def _snapshot(
        self,
        *,
        id: SnapshotId | None = None,
        target: TargetId | None = None,
        media_type: MediaType = MediaType.EBOOK,
        lifecycle: ItemLifecycle = ItemLifecycle.ACTIVE,
        metadata: NormalizedMetadata | None = None,
        files: tuple[FileReference, ...] | None = None,
        cover: CoverReference | None = None,
        provenance: ObservationProvenance | None = None,
        observed_at: datetime | None = None,
        service_release: ServiceRelease | None = None,
        concurrency_token: ConcurrencyToken | None = None,
        capabilities: AdapterCapabilities | None = None,
    ) -> ItemSnapshot:
        return ItemSnapshot.create(
            id=id or SnapshotId("snapshot-one"),
            target=target or self.target,
            media_type=media_type,
            lifecycle=lifecycle,
            metadata=metadata
            or NormalizedMetadata((MetadataField(FieldName.TITLE, ("Example",)),)),
            files=files
            if files is not None
            else (FileReference("books", "fiction/example.epub"),),
            cover=cover or CoverReference("private-cover", self.fingerprint),
            provenance=provenance or self._provenance(observed_at=observed_at),
            service_release=service_release
            or ServiceRelease(self.adapter, ServiceVersion("service-v1")),
            concurrency_token=concurrency_token or ConcurrencyToken("token-value"),
            capabilities=capabilities or self._capabilities(),
        )


if __name__ == "__main__":
    unittest.main()
