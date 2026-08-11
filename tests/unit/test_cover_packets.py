from __future__ import annotations

import hashlib
import io
import inspect
import json
import struct
import sys
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path

from PIL import Image

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "workbench"))

from mechanical_librarian_workbench.contained_reviewer_contract import (  # noqa: E402
    ContainedReviewRequest,
    admit_cover_review_request,
)
from mechanical_librarian_workbench.cover_packets import (  # noqa: E402
    MAX_COVER_PREVIEWS,
    prepare_cover_packet,
)
from mechanical_librarian_workbench import cover_packets as packet_builder  # noqa: E402
from mechanical_librarian_workbench import reviewer as reviewer_binding  # noqa: E402
from mechanical_librarian_workbench import (  # noqa: E402
    CoverPacketRequest,
    IsolatedNetworkProfile,
    WorkbenchContext,
    WorkbenchOperations,
    execute,
)


def _preview(
    index: int,
    *,
    size: int | None = None,
    width: int = 1,
    height: int = 1,
    mode: str = "RGBA",
) -> bytes:
    output = io.BytesIO()
    color = (
        (index % 251, (index * 3) % 251, (index * 7) % 251, 255)
        if mode == "RGBA"
        else (index % 251, (index * 3) % 251, (index * 7) % 251)
    )
    Image.new(mode, (width, height), color).save(
        output, "PNG", optimize=False, compress_level=9
    )
    payload = output.getvalue()
    if size is None:
        return payload
    padding_length = size - len(payload) - 12
    if padding_length < 0:
        raise ValueError("requested PNG size is too small")
    chunk_type = b"vpAg"
    chunk_data = bytes([index % 251]) * padding_length
    chunk = (
        struct.pack(">I", len(chunk_data))
        + chunk_type
        + chunk_data
        + struct.pack(">I", zlib.crc32(chunk_type + chunk_data))
    )
    return payload[:-12] + chunk + payload[-12:]


def _source(
    index: int, payload: bytes, *, incumbent: bool = False, embedded: bool = False
) -> CoverSource:
    artifact = CoverArtifact.create(
        f"original-{index}".encode(), payload, "image/png", 300 + index, 450 + index
    )
    kind = CoverSourceKind.ABS if incumbent else CoverSourceKind.EPUB if embedded else (
        CoverSourceKind.HARDCOVER if index % 2 else CoverSourceKind.OPEN_LIBRARY
    )
    provenance = None if incumbent or embedded else CoverProvenance(
        kind.value,
        CoverDesignation.EXACT_EDITION if index % 3 == 0 else CoverDesignation.WORK,
        f"work-{index}",
        f"edition-{index}" if index % 3 == 0 else None,
    )
    return CoverSource(
        cover_source_id(kind, f"source-{index}"),
        kind,
        CoverSourceState.RETRIEVED,
        artifact,
        provenance=provenance,
    )


def _report(sources: tuple[CoverSource, ...]) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "synthetic", "books", "item", default_target_fields("Synthetic title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
        cover_sources=tuple(sorted(sources, key=lambda value: value.source_id)),
    )


def _packet_case(count: int = 2):
    payloads = tuple(_preview(index) for index in range(1, count + 1))
    sources = tuple(_source(index, payload) for index, payload in enumerate(payloads, 1))
    report = _report(sources)
    by_artifact = {
        source.artifact.artifact_id: payload
        for source, payload in zip(sources, payloads, strict=True)
        if source.artifact is not None
    }
    packet = prepare_cover_packet(
        report.to_json(), lambda artifact, _variant: by_artifact[artifact]
    )
    images = tuple(
        (value.reference, value.digest, value.payload) for value in packet.images
    )
    return packet, images


def _rebind_packet(value: dict[str, object]) -> str:
    body = {
        "candidates": value["candidates"],
        "omissions": value["omissions"],
        "schema_version": value["schema_version"],
    }
    value["packet_reference"] = "cover_packet_" + hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _expanded_packet(packet: str, count: int, *, size: int | None = None):
    value = json.loads(packet)
    template = value["candidates"][0]
    candidates = []
    images = []
    for index in range(1, count + 1):
        payload = _preview(index, size=size)
        digest = hashlib.sha256(payload).hexdigest()
        reference = "cover_ref_" + f"{index:064x}"
        candidate = json.loads(json.dumps(template))
        candidate.update({
            "incumbent": False,
            "preview_bytes": len(payload),
            "preview_sha256": digest,
            "reference": reference,
        })
        candidates.append(candidate)
        images.append((reference, digest, payload))
    value["candidates"] = candidates
    value["omissions"] = []
    return _rebind_packet(value), tuple(images)


def _group_candidate(
    index: int,
    *,
    size: int | None = None,
    duplicates: int = 1,
    width: int = 1000,
    height: int = 1000,
    preview_width: int = 1,
    preview_height: int = 1,
):
    payload = _preview(
        index, size=size, width=preview_width, height=preview_height
    )
    first = _source(index, payload, embedded=True)
    sources = [first]
    references = ["cover_ref_" + f"{index * 10:064x}"]
    for member in range(1, duplicates):
        sources.append(CoverSource(
            cover_source_id(CoverSourceKind.EPUB, f"group-{index}-member-{member}"),
            CoverSourceKind.EPUB,
            CoverSourceState.RETRIEVED,
            first.artifact,
        ))
        references.append("cover_ref_" + f"{index * 10 + member:064x}")
    return packet_builder._Candidate(
        reference=references[0],
        digest=hashlib.sha256(payload).hexdigest(),
        payload=payload,
        width=width,
        height=height,
        preview_width=preview_width,
        preview_height=preview_height,
        incumbent=False,
        sources=sources,
        references=references,
    )


def _admit_produced(packet) -> object:
    return admit_cover_review_request(
        "Inspect.",
        packet.document,
        '{"additionalProperties":false,"properties":{},"type":"object"}',
        tuple(
            (value.reference, value.digest, value.payload)
            for value in packet.images
        ),
    )


class CoverPacketTests(unittest.TestCase):
    def test_five_candidate_scenario_binds_packet_attempt_and_image_order(self) -> None:
        payloads = [_preview(index) for index in range(5)]
        sources = [_source(0, payloads[0], incumbent=True)]
        sources.extend(_source(index, payloads[index]) for index in range(1, 5))
        report = _report(tuple(sources))
        by_artifact = {
            source.artifact.artifact_id: payload
            for source, payload in zip(sources, payloads, strict=True)
            if source.artifact is not None
        }
        packet = prepare_cover_packet(
            report.to_json(), lambda artifact, _variant: by_artifact[artifact]
        )
        observed: list[tuple[object, ...]] = []

        def operation(*_args: object) -> str:
            return '{"ok":true}'

        operations = WorkbenchOperations(
            *(operation for _ in WorkbenchOperations.__dataclass_fields__)
        )
        operations = replace(
            operations,
            reopen_metadata_report=lambda *_: report.to_json(),
            reopen_cover_preview=lambda _db, _artifacts, _report, artifact, _variant: by_artifact[artifact],
            run_cover_packet_reviewer=lambda *args: observed.append(args) or '{"status":"completed"}',
        )
        with WorkbenchContext.temporary() as context:
            files = {
                "contained-reviewer-profile.json": b"{}",
                "cover-reviewer-instruction.txt": b"Inspect the ordered covers.",
                "cover-reviewer-output-schema.json": b'{"additionalProperties":false,"properties":{},"type":"object"}',
            }
            for name, payload in files.items():
                path = context.path(name)
                path.write_bytes(payload)
                if name.endswith("profile.json"):
                    path.chmod(0o600)
            context.network = IsolatedNetworkProfile(
                "isolated-test", True, True,
                tuple(sorted({
                    *(hashlib.sha256(value).hexdigest() for value in files.values()),
                    packet.packet_digest,
                })),
            )
            result = execute(context, CoverPacketRequest(report.report_id), operations)
            self.assertEqual(result.name, "cover-packet-review")
            self.assertEqual(len(tuple(context.path("attempts").iterdir())), 1)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][2], packet.document.encode())
        image_arguments = observed[0][4]
        self.assertIsInstance(image_arguments, tuple)
        assert isinstance(image_arguments, tuple)
        self.assertEqual(
            [(value[0], value[1]) for value in image_arguments],
            [(value.reference, value.digest) for value in packet.images],
        )
        self.assertEqual(len(image_arguments), 5)

    def test_only_cover_entry_accepts_exact_packet_bound_images(self) -> None:
        packet, exact = _packet_case()

        self.assertNotIn(
            "image_payloads",
            inspect.signature(reviewer_binding.run_contained_reviewer).parameters,
        )
        with self.assertRaises(TypeError):
            reviewer_binding.run_contained_reviewer(
                b"", "invoke", b"instruction", b"{}", b"{}", exact  # type: ignore[call-arg]
            )
        admitted = admit_cover_review_request(
            "Inspect.", packet.document,
            '{"additionalProperties":false,"properties":{},"type":"object"}',
            exact,
        )
        self.assertEqual(
            [(value.reference, value.digest) for value in admitted.images],
            [(value.reference, value.digest) for value in packet.images],
        )
        mismatches = (
            exact[:-1],
            tuple(reversed(exact)),
            ((exact[0][0], exact[1][1], exact[0][2]), exact[1]),
            exact + (("cover_ref_" + "f" * 64, hashlib.sha256(_preview(3)).hexdigest(), _preview(3)),),
        )
        for mismatch in mismatches:
            with self.subTest(binding=[value[:2] for value in mismatch]), self.assertRaisesRegex(
                Exception, "response_format"
            ):
                reviewer_binding.run_cover_packet_reviewer(
                    b"", b"instruction", packet.document.encode(), b"{}", mismatch
                )

    def test_admission_fully_decodes_exact_rgba_png_preview(self) -> None:
        packet, _ = _packet_case(1)
        original = json.loads(packet.document)
        reference = original["candidates"][0]["reference"]
        schema = '{"additionalProperties":false,"properties":{},"type":"object"}'
        valid = _preview(1)

        def bound(
            payload: bytes, *, preview_width: int = 1, preview_height: int = 1
        ) -> tuple[str, tuple[tuple[str, str, bytes], ...]]:
            value = json.loads(json.dumps(original))
            digest = hashlib.sha256(payload).hexdigest()
            value["candidates"][0].update({
                "preview_bytes": len(payload),
                "preview_height": preview_height,
                "preview_sha256": digest,
                "preview_width": preview_width,
            })
            return _rebind_packet(value), ((reference, digest, payload),)

        def corrupt_idat(payload: bytes) -> bytes:
            changed = bytearray(payload)
            offset = 8
            while offset < len(changed):
                length = struct.unpack(">I", changed[offset:offset + 4])[0]
                chunk_type = bytes(changed[offset + 4:offset + 8])
                data_start = offset + 8
                data_end = data_start + length
                if chunk_type == b"IDAT":
                    changed[data_start] ^= 0xFF
                    changed[data_end:data_end + 4] = struct.pack(
                        ">I", zlib.crc32(chunk_type + changed[data_start:data_end])
                    )
                    return bytes(changed)
                offset = data_end + 4
            raise AssertionError("test PNG has no IDAT")

        def oversized_header(payload: bytes) -> bytes:
            changed = bytearray(payload)
            changed[16:20] = struct.pack(">I", 321)
            changed[29:33] = struct.pack(">I", zlib.crc32(changed[12:29]))
            return bytes(changed)

        def chunks(payload: bytes) -> list[tuple[bytes, bytes]]:
            values: list[tuple[bytes, bytes]] = []
            offset = 8
            while offset < len(payload):
                length = struct.unpack(">I", payload[offset:offset + 4])[0]
                chunk_type = payload[offset + 4:offset + 8]
                data_start = offset + 8
                data_end = data_start + length
                values.append((chunk_type, payload[data_start:data_end]))
                offset = data_end + 4
            return values

        def render(values: list[tuple[bytes, bytes]]) -> bytes:
            result = bytearray(b"\x89PNG\r\n\x1a\n")
            for chunk_type, data in values:
                result.extend(struct.pack(">I", len(data)))
                result.extend(chunk_type)
                result.extend(data)
                result.extend(struct.pack(">I", zlib.crc32(chunk_type + data)))
            return bytes(result)

        canonical_chunks = chunks(valid)
        idat_index = next(
            index for index, value in enumerate(canonical_chunks)
            if value[0] == b"IDAT"
        )
        idat_data = canonical_chunks[idat_index][1]
        split = max(1, len(idat_data) // 2)
        nonconsecutive_idat = render([
            *canonical_chunks[:idat_index],
            (b"IDAT", idat_data[:split]),
            (b"vpAg", b"bounded"),
            (b"IDAT", idat_data[split:]),
            *canonical_chunks[idat_index + 1:],
        ])
        plte = (b"PLTE", b"\x00\x00\x00")
        plte_after_idat = render([*canonical_chunks[:-1], plte, canonical_chunks[-1]])
        duplicate_plte = render([
            *canonical_chunks[:idat_index], plte, plte,
            *canonical_chunks[idat_index:],
        ])
        lowercase_reserved = render([
            *canonical_chunks[:idat_index], (b"vpag", b"bounded"),
            *canonical_chunks[idat_index:],
        ])
        unknown_critical = render([
            *canonical_chunks[:idat_index], (b"ABCD", b"bounded"),
            *canonical_chunks[idat_index:],
        ])
        ihdr_not_first = render([(b"vpAg", b"bounded"), *canonical_chunks])
        duplicate_ihdr = render([
            canonical_chunks[0], canonical_chunks[0], *canonical_chunks[1:],
        ])
        iend_before_data = render([
            canonical_chunks[0], canonical_chunks[-1], *canonical_chunks[1:-1],
        ])

        malformed = {
            "forged_signature": b"\x89PNG\r\n\x1a\nforged",
            "truncated": valid[:-4],
            "corrupt_crc": valid[:29] + bytes([valid[29] ^ 1]) + valid[30:],
            "trailing_bytes": valid + b"trailing",
            "wrong_mode": _preview(1, mode="RGB"),
            "decompression_failure": corrupt_idat(valid),
            "decompression_bomb_dimensions": oversized_header(valid),
            "nonconsecutive_idat": nonconsecutive_idat,
            "plte_after_idat": plte_after_idat,
            "duplicate_plte": duplicate_plte,
            "lowercase_reserved_bit": lowercase_reserved,
            "unknown_critical_chunk": unknown_critical,
            "ihdr_not_first": ihdr_not_first,
            "duplicate_ihdr": duplicate_ihdr,
            "iend_before_idat": iend_before_data,
        }
        for name, payload in malformed.items():
            document, images = bound(payload)
            with self.subTest(case=name), self.assertRaisesRegex(
                Exception, "response_format"
            ):
                admit_cover_review_request("Inspect.", document, schema, images)

        document, images = bound(valid, preview_width=2)
        with self.assertRaisesRegex(Exception, "response_format"):
            admit_cover_review_request("Inspect.", document, schema, images)

        admitted_document = json.loads(packet.document)
        self.assertEqual(
            (
                admitted_document["candidates"][0]["width"],
                admitted_document["candidates"][0]["height"],
            ),
            (301, 451),
        )
        self.assertEqual(
            (
                admitted_document["candidates"][0]["preview_width"],
                admitted_document["candidates"][0]["preview_height"],
            ),
            (1, 1),
        )

    def test_original_dimensions_retain_technical_quality_order(self) -> None:
        packet = packet_builder._finalize_cover_packet(
            [
                _group_candidate(1, width=100, height=100),
                _group_candidate(2, width=1000, height=1000),
            ],
            [],
        )
        admitted = _admit_produced(packet)
        document = json.loads(packet.document)

        self.assertEqual(
            [(value["width"], value["height"]) for value in document["candidates"]],
            [(1000, 1000), (100, 100)],
        )
        self.assertEqual(
            [
                (value["preview_width"], value["preview_height"])
                for value in document["candidates"]
            ],
            [(1, 1), (1, 1)],
        )
        self.assertEqual(len(admitted.images), 2)  # type: ignore[attr-defined]

    def test_cover_entry_rejects_images_bound_to_non_packet_input(self) -> None:
        payload = _preview(1)
        binding = ((
            "cover_ref_" + "a" * 64,
            hashlib.sha256(payload).hexdigest(),
            payload,
        ),)
        forged = json.dumps(
            {"schema_version": "some-other-input-v1"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
        with self.assertRaisesRegex(Exception, "response_format"):
            reviewer_binding.run_cover_packet_reviewer(
                b"", b"instruction", forged, b"{}", binding
            )

    def test_packet_retains_incumbent_deduplicates_and_shortlists_deterministically(self) -> None:
        payloads = [_preview(index) for index in range(10)]
        sources = [_source(0, payloads[0], incumbent=True)]
        sources.extend(_source(index, payloads[index]) for index in range(1, 9))
        duplicate = _source(9, payloads[1], embedded=True)
        sources.append(duplicate)
        report = _report(tuple(sources))
        by_artifact: dict[str, bytes] = {}
        for source in sources:
            artifact = source.artifact
            assert artifact is not None
            by_artifact[artifact.artifact_id] = payloads[1] if source is duplicate else next(
                value for value in payloads
                if hashlib.sha256(value).hexdigest() == artifact.preview_digest
            )

        first = prepare_cover_packet(report.to_json(), lambda artifact, _variant: by_artifact[artifact])
        second = prepare_cover_packet(report.to_json(), lambda artifact, _variant: by_artifact[artifact])
        packet = json.loads(first.document)

        self.assertEqual(first.document, second.document)
        self.assertEqual(len(first.images), 9)
        self.assertLessEqual(len(first.images), MAX_COVER_PREVIEWS)
        self.assertTrue(packet["candidates"][0]["incumbent"])
        self.assertEqual(
            [value["reference"] for value in packet["candidates"]],
            [value.reference for value in first.images],
        )
        self.assertIn("duplicate_preview", {value["reason"] for value in packet["omissions"]})
        self.assertNotIn("Synthetic title", first.document)

    def test_changed_preview_and_overbound_incumbent_fail_closed(self) -> None:
        source = _source(0, _preview(0), incumbent=True)
        report = _report((source,))
        with self.assertRaisesRegex(ValueError, "digest changed"):
            prepare_cover_packet(report.to_json(), lambda *_: _preview(9))

        overbound = _source(0, _preview(0, size=1_048_577), incumbent=True)
        with self.assertRaisesRegex(ValueError, "incumbent exceeds"):
            prepare_cover_packet(
                _report((overbound,)).to_json(), lambda *_: overbound.artifact and _preview(0, size=1_048_577)
            )

    def test_image_order_and_digests_bind_the_request_identity(self) -> None:
        first_packet, first_images = _packet_case()
        second_packet, second_images = _packet_case(3)
        schema = json.dumps(
            {"additionalProperties": False, "properties": {}, "type": "object"},
            separators=(",", ":"), sort_keys=True,
        )
        request = admit_cover_review_request(
            "Inspect.", first_packet.document, schema, first_images
        )
        changed = admit_cover_review_request(
            "Inspect.", second_packet.document, schema, second_images
        )
        self.assertNotEqual(request.digest, changed.digest)

    def test_image_count_and_aggregate_byte_limits_are_absolute(self) -> None:
        schema = json.dumps(
            {"additionalProperties": False, "properties": {}, "type": "object"},
            separators=(",", ":"), sort_keys=True,
        )

        packet, _ = _packet_case()
        document, images = _expanded_packet(packet.document, 12)
        self.assertEqual(
            len(admit_cover_review_request("Inspect.", document, schema, images).images),
            12,
        )
        over_count_document, over_count_images = _expanded_packet(packet.document, 13)
        with self.assertRaisesRegex(Exception, "response_format"):
            admit_cover_review_request(
                "Inspect.", over_count_document, schema, over_count_images
            )
        over_bytes_document, over_bytes_images = _expanded_packet(
            packet.document, 9, size=1_048_576
        )
        with self.assertRaisesRegex(Exception, "response_format"):
            admit_cover_review_request(
                "Inspect.", over_bytes_document, schema, over_bytes_images
            )
        with self.assertRaises(TypeError):
            ContainedReviewRequest("Inspect.", '{"packet":true}', schema, images)  # type: ignore[call-arg]

    def test_full_packet_parser_rejects_malformed_fields_and_invariants(self) -> None:
        packet, images = _packet_case()
        schema = '{"additionalProperties":false,"properties":{},"type":"object"}'
        original = json.loads(packet.document)
        mutations = {
            "packet_reference": lambda value: value.__setitem__("packet_reference", "cover_packet_bad"),
            "packet_binding": lambda value: value.__setitem__("packet_reference", "cover_packet_" + "0" * 64),
            "omission": lambda value: value.__setitem__("omissions", [{"reason": "invented", "reference": "cover_ref_" + "f" * 64}]),
            "omission_schema": lambda value: value.__setitem__("omissions", [{"extra": True, "reason": "shortlist_limit", "reference": "cover_ref_" + "f" * 64}]),
            "dimension": lambda value: value["candidates"][0].__setitem__("width", 0),
            "incumbent": lambda value: value["candidates"][0].__setitem__("incumbent", True),
            "provenance": lambda value: value["candidates"][0]["provenance"][0].__setitem__("provider", " invalid"),
            "byte_fact": lambda value: value["candidates"][0].__setitem__("preview_bytes", 31),
        }
        for name, mutate in mutations.items():
            value = json.loads(json.dumps(original))
            mutate(value)
            document = (
                json.dumps(value, separators=(",", ":"), sort_keys=True)
                if name in {"packet_reference", "packet_binding"} else _rebind_packet(value)
            )
            with self.subTest(field=name), self.assertRaisesRegex(Exception, "response_format"):
                admit_cover_review_request("Inspect.", document, schema, images)

    def test_context_projection_uses_exact_domain_privacy_and_unicode_policy(self) -> None:
        packet, images = _packet_case()
        schema = '{"additionalProperties":false,"properties":{},"type":"object"}'

        def with_context(value: str) -> str:
            document = json.loads(packet.document)
            document["candidates"][0]["provenance"][0]["context"] = [
                {"field": "publisher", "value": value}
            ]
            return _rebind_packet(document)

        admitted = admit_cover_review_request(
            "Inspect.", with_context("Éditions 東京"), schema, images
        )
        self.assertIn("Éditions 東京", admitted.input_document)
        invalid = {
            "private_path": "/private/library/title.epub",
            "credential": "api_key=privatevalue",
            "bidi": "safe\u202eunsafe",
            "semantic_separator": "safe\u2028unsafe",
            "forbidden_unicode": "safe\ufdd0unsafe",
        }
        for name, value in invalid.items():
            with self.subTest(value=name), self.assertRaisesRegex(
                Exception, "response_format"
            ):
                admit_cover_review_request(
                    "Inspect.", with_context(value), schema, images
                )

    def test_conjoint_candidate_and_image_reorder_is_rejected(self) -> None:
        packet, images = _packet_case(3)
        document = json.loads(packet.document)
        document["candidates"].reverse()
        reordered = tuple(reversed(images))
        with self.assertRaisesRegex(Exception, "response_format"):
            admit_cover_review_request(
                "Inspect.", _rebind_packet(document),
                '{"additionalProperties":false,"properties":{},"type":"object"}',
                reordered,
            )

    def test_omission_reasons_require_and_accept_reachable_preconditions(self) -> None:
        schema = '{"additionalProperties":false,"properties":{},"type":"object"}'
        packet, images = _packet_case()
        retained = json.loads(packet.document)["candidates"][0]
        omitted_reference = "cover_ref_" + "f" * 64

        def document_with(base: str, omission: dict[str, object]) -> str:
            value = json.loads(base)
            value["omissions"] = [omission]
            return _rebind_packet(value)

        source_reasons = (
            "source_absent", "source_animated_or_multiframe", "source_decode_failed",
            "source_invalid_declaration", "source_oversized", "source_rejected",
            "source_source_changed", "source_timeout", "source_truncated",
            "source_unavailable", "source_unsafe_remote", "source_unsupported_format",
        )
        for reason in source_reasons:
            admitted = admit_cover_review_request(
                "Inspect.",
                document_with(packet.document, {
                    "reason": reason, "reference": omitted_reference,
                }),
                schema,
                images,
            )
            self.assertEqual(admitted.images[0].digest, images[0][1])

        reachable = (
            (packet.document, images, {
                "preview_sha256": retained["preview_sha256"],
                "reason": "duplicate_preview",
                "reference": omitted_reference,
                "retained_reference": retained["reference"],
            }),
            (packet.document, images, {
                "preview_bytes": 1_048_577,
                "reason": "preview_byte_limit",
                "reference": omitted_reference,
            }),
        )
        for base, bound_images, omission in reachable:
            admit_cover_review_request(
                "Inspect.", document_with(base, omission), schema, bound_images
            )

        full_document, full_images = _expanded_packet(
            packet.document, 8, size=1_048_576
        )
        admit_cover_review_request(
            "Inspect.",
            document_with(full_document, {
                "preview_bytes": 1,
                "reason": "aggregate_byte_limit",
                "reference": omitted_reference,
            }),
            schema,
            full_images,
        )
        capped_document, capped_images = _expanded_packet(packet.document, 12)
        admit_cover_review_request(
            "Inspect.",
            document_with(capped_document, {
                "preview_bytes": 32,
                "reason": "shortlist_limit",
                "reference": omitted_reference,
            }),
            schema,
            capped_images,
        )

        impossible = (
            {"preview_bytes": 32, "reason": "shortlist_limit", "reference": omitted_reference},
            {"preview_bytes": 32, "reason": "aggregate_byte_limit", "reference": omitted_reference},
            {"preview_bytes": 32, "reason": "preview_byte_limit", "reference": omitted_reference},
            {
                "preview_sha256": "0" * 64,
                "reason": "duplicate_preview",
                "reference": omitted_reference,
                "retained_reference": retained["reference"],
            },
        )
        for omission in impossible:
            with self.subTest(reason=omission["reason"]), self.assertRaisesRegex(
                Exception, "response_format"
            ):
                admit_cover_review_request(
                    "Inspect.", document_with(packet.document, omission), schema, images
                )

    def test_digest_equal_indistinguishable_provenance_is_emitted_once(self) -> None:
        payload = _preview(7)
        first = _source(7, payload)
        second = CoverSource(
            cover_source_id(first.kind, "same-semantics-other-source"),
            first.kind,
            first.state,
            first.artifact,
            provenance=first.provenance,
        )
        report = _report((first, second))
        assert first.artifact is not None
        packet = prepare_cover_packet(
            report.to_json(), lambda *_: payload
        )
        document = json.loads(packet.document)
        self.assertEqual(len(document["candidates"]), 1)
        self.assertEqual(len(document["candidates"][0]["provenance"]), 1)
        self.assertEqual(document["omissions"][0]["reason"], "duplicate_preview")
        admitted = admit_cover_review_request(
            "Inspect.",
            packet.document,
            '{"additionalProperties":false,"properties":{},"type":"object"}',
            tuple(
                (value.reference, value.digest, value.payload)
                for value in packet.images
            ),
        )
        self.assertEqual(len(admitted.images), 1)

    def test_duplicate_group_retained_representative_binds_after_selection(self) -> None:
        packet = packet_builder._finalize_cover_packet(
            [_group_candidate(1, duplicates=2)], []
        )
        admitted = _admit_produced(packet)
        document = json.loads(packet.document)
        self.assertEqual(len(admitted.images), 1)  # type: ignore[attr-defined]
        self.assertEqual(document["omissions"], [{
            "preview_sha256": document["candidates"][0]["preview_sha256"],
            "reason": "duplicate_preview",
            "reference": "cover_ref_" + f"{11:064x}",
            "retained_reference": document["candidates"][0]["reference"],
        }])

    def test_duplicate_group_excluded_by_preview_limit_has_no_unbound_representative(self) -> None:
        packet = packet_builder._finalize_cover_packet(
            [
                _group_candidate(1),
                _group_candidate(2, size=1_048_577, duplicates=2, width=1, height=1),
            ],
            [],
        )
        _admit_produced(packet)
        document = json.loads(packet.document)
        self.assertEqual(
            [value["reason"] for value in document["omissions"]],
            ["preview_byte_limit", "preview_byte_limit"],
        )
        self.assertNotIn("duplicate_preview", packet.document)

    def test_duplicate_group_excluded_by_aggregate_limit_has_no_unbound_representative(self) -> None:
        retained = [
            _group_candidate(index, size=1_048_576)
            for index in range(1, 9)
        ]
        packet = packet_builder._finalize_cover_packet(
            [*retained, _group_candidate(20, duplicates=2, width=1, height=1)],
            [],
        )
        _admit_produced(packet)
        document = json.loads(packet.document)
        self.assertEqual(len(document["candidates"]), 8)
        self.assertEqual(
            [value["reason"] for value in document["omissions"]],
            ["aggregate_byte_limit", "aggregate_byte_limit"],
        )
        self.assertNotIn("duplicate_preview", packet.document)

    def test_duplicate_group_excluded_by_shortlist_has_no_unbound_representative(self) -> None:
        retained = [_group_candidate(index) for index in range(1, 13)]
        packet = packet_builder._finalize_cover_packet(
            [*retained, _group_candidate(20, duplicates=2, width=1, height=1)],
            [],
        )
        _admit_produced(packet)
        document = json.loads(packet.document)
        self.assertEqual(len(document["candidates"]), 12)
        self.assertEqual(
            [value["reason"] for value in document["omissions"]],
            ["shortlist_limit", "shortlist_limit"],
        )
        self.assertNotIn("duplicate_preview", packet.document)


if __name__ == "__main__":
    unittest.main()
