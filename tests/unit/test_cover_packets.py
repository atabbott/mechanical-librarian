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
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageCms

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
    ClaimKind,
    Comparison,
    FileFacts,
    IdentifierRelation,
    FieldState,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderQueryKind,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
    provider_lane_candidate_reference,
)

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "workbench"))

from mechanical_librarian_workbench.contained_reviewer_contract import (  # noqa: E402
    ContainedReviewRequest,
    admit_cover_review_request,
    canonical_json,
)
from mechanical_librarian_workbench.cover_observation import (  # noqa: E402
    INSTRUCTION,
    LEGACY_INSTRUCTION_SHA256,
    OUTPUT_SCHEMA_VERSION,
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
    TAXONOMY,
    TAXONOMY_DOCUMENT,
    TAXONOMY_REFERENCE,
    observe_cover_packet,
    _output_schema,
)
from mechanical_librarian_workbench.cover_packets import (  # noqa: E402
    MAX_COVER_PREVIEWS,
    prepare_cover_packet,
)
from mechanical_librarian_workbench.cover_identity import (  # noqa: E402
    SCHEMA_VERSION,
    _CLASSIFIER_REFERENCE,
    _preview_reference,
    _classify_sources,
    classify_cover_identity,
)
from mechanical_librarian_workbench.cover_policy import (  # noqa: E402
    CoverPolicy,
    adjudicate_cover_policy,
)
from mechanical_librarian_workbench import cover_packets as packet_builder  # noqa: E402
from mechanical_librarian_workbench import reviewer as reviewer_binding  # noqa: E402
from mechanical_librarian_workbench import contained_reviewer as reviewer_adapter  # noqa: E402
from mechanical_librarian_workbench import (  # noqa: E402
    CoverIdentityRequest,
    CoverObservationRequest,
    CoverPacketRequest,
    IsolatedNetworkProfile,
    WorkbenchContext,
    WorkbenchFailure,
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
    icc_profile: bytes | None = None,
) -> bytes:
    output = io.BytesIO()
    color = (
        (index % 251, (index * 3) % 251, (index * 7) % 251, 255)
        if mode == "RGBA"
        else (index % 251, (index * 3) % 251, (index * 7) % 251)
    )
    save_options: dict[str, object] = {"optimize": False, "compress_level": 9}
    if icc_profile is not None:
        save_options["icc_profile"] = icc_profile
    Image.new(mode, (width, height), color).save(output, "PNG", **save_options)
    payload = output.getvalue()
    if size is None:
        return payload
    raw = b"\x00" + bytes(color)
    # Inflate the valid zlib stream with empty stored blocks, then divide that
    # one stream across a small consecutive IDAT run. This reaches exact byte
    # boundaries without ancillary chunks or bytes after the zlib stream.
    extra_idat_chunks = next(
        value for value in range(5)
        if (73 + 12 * value) % 5 == size % 5
    )
    fixed_bytes = 73 + 12 * extra_idat_chunks
    empty_blocks, remainder = divmod(size - fixed_bytes, 5)
    if empty_blocks < 0 or remainder:
        raise ValueError("requested PNG size is too small")
    compressed = (
        b"\x78\x01"
        + b"\x00\x00\x00\xff\xff" * empty_blocks
        + b"\x01"
        + len(raw).to_bytes(2, "little")
        + (0xFFFF - len(raw)).to_bytes(2, "little")
        + raw
        + struct.pack(">I", zlib.adler32(raw))
    )

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + chunk_type + data
            + struct.pack(">I", zlib.crc32(chunk_type + data))
        )

    idat_count = extra_idat_chunks + 1
    boundaries = [len(compressed) * value // idat_count for value in range(idat_count + 1)]
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    result = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    result += b"".join(
        chunk(b"IDAT", compressed[boundaries[value]:boundaries[value + 1]])
        for value in range(idat_count)
    )
    result += chunk(b"IEND", b"")
    if len(result) != size:
        raise AssertionError("exact PNG construction failed")
    return result


def _animated_preview() -> bytes:
    output = io.BytesIO()
    first = Image.new("RGBA", (4, 4), (20, 40, 60, 255))
    second = Image.new("RGBA", (4, 4), (80, 100, 120, 255))
    first.save(
        output,
        "PNG",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )
    payload = output.getvalue()
    with Image.open(io.BytesIO(payload)) as image:
        if image.n_frames != 2:
            raise AssertionError("animated PNG construction failed")
    return payload


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


def _classify_identity(
    report_document: str,
    packet_document: str,
    images: tuple[tuple[str, str, bytes], ...],
    read_preview,
):
    return classify_cover_identity(
        report_document,
        packet_document,
        images,
        read_preview=read_preview,
    )


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
        source_digest = hashlib.sha256(payload).hexdigest()
        normalized = packet_builder.normalize_cover_preview(
            payload, source_sha256=source_digest
        )
        reference = "cover_ref_" + f"{index:064x}"
        source_bindings = [{
            "preview_sha256": source_digest,
            "reference": reference,
        }]
        transport_reference = packet_builder.cover_transport_reference(
            reference, source_bindings, normalized.digest
        )
        candidate = json.loads(json.dumps(template))
        candidate.update({
            "incumbent": False,
            "preview_bytes": len(normalized.payload),
            "preview_sha256": source_digest,
            "reference": reference,
            "source_bindings": source_bindings,
            "transport_reference": transport_reference,
            "transport_sha256": normalized.digest,
        })
        candidates.append(candidate)
        images.append((transport_reference, normalized.digest, normalized.payload))
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
    source_payload = _preview(
        index, size=size, width=preview_width, height=preview_height
    )
    first = _source(index, source_payload, embedded=True)
    source_digest = hashlib.sha256(source_payload).hexdigest()
    payload = source_payload
    if size is None:
        payload = packet_builder.normalize_cover_preview(
            source_payload, source_sha256=source_digest
        ).payload
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
        source_digest=source_digest,
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


def _observer_result(
    profile: bytes,
    instruction: bytes,
    packet: bytes,
    schema: bytes,
    images: tuple[tuple[str, str, bytes], ...],
    *,
    mutate: object | None = None,
) -> str:
    admitted = admit_cover_review_request(
        instruction.decode(), packet.decode(), schema.decode(), images
    )
    candidates = json.loads(packet)["candidates"]
    result: dict[str, object] = {
        "observations": [
            {
                "candidate_reference": value["reference"],
                "composition": "minimal" if index % 2 else "dense",
                "medium": "illustrated" if index % 2 else "photographic",
                "promotional_text": "absent",
                "tie_in_branding": "unknown",
            }
            for index, value in enumerate(candidates, start=1)
        ],
        "schema_version": OUTPUT_SCHEMA_VERSION,
    }
    if mutate is not None:
        assert callable(mutate)
        mutate(result)
    image_binding = canonical_json(
        [
            {"reference": reference, "sha256": digest}
            for reference, digest, _payload in images
        ]
    )
    execution_identity = canonical_json(
        {
            "profile_sha256": hashlib.sha256(profile).hexdigest(),
            "request_sha256": admitted.digest,
        }
    )
    return canonical_json(
        {
            "attempt_evidence": [{"dispatched": True}],
            "execution_sha256": hashlib.sha256(execution_identity.encode()).hexdigest(),
            "image_binding_sha256": hashlib.sha256(image_binding.encode()).hexdigest(),
            "image_count": len(images),
            "input_sha256": hashlib.sha256(packet).hexdigest(),
            "request_sha256": admitted.digest,
            "result": result,
            "result_sha256": hashlib.sha256(canonical_json(result).encode()).hexdigest(),
            "schema_version": "cover-contained-reviewer-result-v1",
            "status": "completed",
        }
    )


class CoverPacketTests(unittest.TestCase):
    def test_normalized_transport_has_platform_independent_exact_bytes(self) -> None:
        source = io.BytesIO()
        Image.new("RGBA", (1, 1), (1, 2, 3, 4)).save(source, "PNG")
        source_payload = source.getvalue()

        normalized = packet_builder.normalize_cover_preview(
            source_payload,
            source_sha256=hashlib.sha256(source_payload).hexdigest(),
        )

        expected = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001"
            "08060000001f15c48900000010494441547801010500faff"
            "00010203040019000bb9b0e3eb0000000049454e44ae426082"
        )
        self.assertEqual(normalized.payload, expected)
        self.assertEqual(
            normalized.digest,
            "ade26d473c75b874b4db5ffbf620097659fce1ceae14797efe22dc2e3729e829",
        )
        self.assertEqual((normalized.width, normalized.height), (1, 1))
        with Image.open(io.BytesIO(normalized.payload)) as decoded:
            self.assertEqual(decoded.tobytes(), b"\x01\x02\x03\x04")

    def test_normalized_transport_uses_fixed_stored_blocks_across_rows(self) -> None:
        source = io.BytesIO()
        Image.new("RGBA", (100, 200), (5, 10, 15, 20)).save(source, "PNG")
        source_payload = source.getvalue()

        normalized = packet_builder.normalize_cover_preview(
            source_payload,
            source_sha256=hashlib.sha256(source_payload).hexdigest(),
        )

        self.assertEqual(len(normalized.payload), 80_273)
        self.assertEqual(
            normalized.digest,
            "6edd73f4fa023d64ab145abb3b0170c49f08db1d7ee18aefcfa7d40d8401e928",
        )
        idat_length = struct.unpack(">I", normalized.payload[33:37])[0]
        self.assertEqual(normalized.payload[37:41], b"IDAT")
        compressed = normalized.payload[41:41 + idat_length]
        self.assertEqual(compressed[:7], b"\x78\x01\x00\xff\xff\x00\x00")
        self.assertEqual(
            compressed[2 + 5 + 65_535:2 + 5 + 65_535 + 5],
            b"\x01\x49\x39\xb6\xc6",
        )
        scanlines = zlib.decompress(compressed)
        self.assertEqual(len(scanlines), 80_200)
        for offset in range(0, len(scanlines), 401):
            self.assertEqual(scanlines[offset], 0)
            self.assertEqual(
                scanlines[offset + 1:offset + 401],
                bytes((5, 10, 15, 20)) * 100,
            )

        largest_source = io.BytesIO()
        Image.new("RGBA", (320, 480), (5, 10, 15, 20)).save(
            largest_source, "PNG"
        )
        largest_payload = largest_source.getvalue()
        largest = packet_builder.normalize_cover_preview(
            largest_payload,
            source_sha256=hashlib.sha256(largest_payload).hexdigest(),
        )
        self.assertEqual(len(largest.payload), 614_993)
        self.assertLessEqual(
            len(largest.payload) * MAX_COVER_PREVIEWS,
            packet_builder.MAX_COVER_PACKET_BYTES,
        )

    def test_iccp_preview_is_normalized_for_transport(self) -> None:
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        ).tobytes()
        source_payload = _preview(
            17, width=7, height=9, icc_profile=profile
        )
        source = _source(17, source_payload)
        assert source.artifact is not None

        packet = prepare_cover_packet(
            _report((source,)).to_json(),
            lambda _artifact, _variant: source_payload,
        )

        candidate = json.loads(packet.document)["candidates"][0]
        image = packet.images[0]
        self.assertEqual(
            candidate["preview_sha256"], source.artifact.preview_digest
        )
        self.assertEqual(candidate["transport_sha256"], image.digest)
        self.assertEqual(candidate["transport_reference"], image.reference)
        self.assertNotEqual(image.payload, source_payload)
        with Image.open(io.BytesIO(source_payload)) as original, Image.open(
            io.BytesIO(image.payload)
        ) as normalized:
            self.assertEqual(normalized.mode, "RGBA")
            self.assertEqual(normalized.size, original.size)
            self.assertEqual(normalized.tobytes(), original.tobytes())

        chunk_types: list[bytes] = []
        offset = 8
        while offset < len(image.payload):
            length = struct.unpack(">I", image.payload[offset:offset + 4])[0]
            chunk_types.append(image.payload[offset + 4:offset + 8])
            offset += 12 + length
        self.assertEqual(chunk_types, [b"IHDR", b"IDAT", b"IEND"])
        _admit_produced(packet)

        repeated = prepare_cover_packet(
            _report((source,)).to_json(),
            lambda _artifact, _variant: source_payload,
        )
        self.assertEqual(repeated.document, packet.document)
        self.assertEqual(repeated.images, packet.images)

    def test_normalized_transport_rejects_binding_and_source_substitution(self) -> None:
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        ).tobytes()
        first_payload = _preview(21, width=5, height=6, icc_profile=profile)
        second_payload = _preview(22, width=5, height=6, icc_profile=profile)
        first_source = _source(21, first_payload)
        second_source = _source(22, second_payload)
        first_report = _report((first_source,))
        packet = prepare_cover_packet(
            first_report.to_json(), lambda *_: first_payload
        )
        schema = '{"additionalProperties":false,"properties":{},"type":"object"}'
        images = tuple(
            (value.reference, value.digest, value.payload)
            for value in packet.images
        )

        candidate = json.loads(packet.document)["candidates"][0]
        changed = json.loads(packet.document)
        changed["candidates"][0]["source_bindings"][0]["preview_sha256"] = (
            hashlib.sha256(second_payload).hexdigest()
        )
        with self.assertRaisesRegex(Exception, "response_format"):
            admit_cover_review_request(
                "Inspect.", _rebind_packet(changed), schema, images
            )

        foreign = bytearray(images[0][2])
        foreign[-13] ^= 1
        with self.assertRaisesRegex(Exception, "response_format"):
            admit_cover_review_request(
                "Inspect.", packet.document, schema,
                ((images[0][0], images[0][1], bytes(foreign)),),
            )

        with self.assertRaisesRegex(ValueError, "report binding"):
            classify_cover_identity(
                _report((second_source,)).to_json(),
                packet.document,
                images,
                read_preview=lambda *_: second_payload,
            )

    def test_normalization_fails_closed_on_hostile_source_shapes(self) -> None:
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        ).tobytes()
        valid = _preview(25, width=4, height=4, icc_profile=profile)
        hostile = {
            "animated": _animated_preview(),
            "trailing": valid + b"trailing",
            "wrong_mode": _preview(25, width=4, height=4, mode="RGB", icc_profile=profile),
            "oversized_dimensions": _preview(25, width=321, height=1, icc_profile=profile),
            "corrupt_crc": valid[:29] + bytes([valid[29] ^ 1]) + valid[30:],
        }
        for name, payload in hostile.items():
            source = _source(25, payload)
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "normalization failed"
            ):
                prepare_cover_packet(_report((source,)).to_json(), lambda *_: payload)

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

    def test_cover_retries_only_one_proven_local_cli_abort_with_bound_attempt_evidence(self) -> None:
        packet, images = _packet_case()
        schema = '{"additionalProperties":false,"properties":{"summary":{"type":"string"}},"required":["summary"],"type":"object"}'
        request_digests: list[str] = []
        instances: list[object] = []

        class FakeReviewer:
            def __init__(self, _profile: object) -> None:
                instances.append(self)

            def run_cover(self, request, dispatched):
                request_digests.append(request.digest)
                dispatched(100 + len(instances), 100 + len(instances), "synthetic")
                document = '{"summary":"safe"}'
                return SimpleNamespace(
                    document=document,
                    digest=hashlib.sha256(document.encode()).hexdigest(),
                    attempts=(),
                )

        with (
            mock.patch.object(reviewer_binding, "load_contained_reviewer_profile_bytes", return_value=object()),
            mock.patch.object(reviewer_binding, "ContainedReviewerAdapter", FakeReviewer),
        ):
            envelope = json.loads(reviewer_binding.run_cover_packet_reviewer(
                b"profile", b"Inspect.", packet.document.encode(), schema.encode(), images
            ))
        self.assertEqual(len(instances), 1)
        self.assertEqual(request_digests, [request_digests[0]])
        self.assertEqual(envelope["attempt_evidence"], [])

    def test_cover_retry_is_hard_capped_and_rejects_other_failures(self) -> None:
        packet, images = _packet_case()
        schema = '{"additionalProperties":false,"properties":{"summary":{"type":"string"}},"required":["summary"],"type":"object"}'
        for code, expected_instances, expected_sleep in (
            ("local_cli_abort", 2, 1),
            ("runtime", 1, 0),
            ("response_format", 1, 0),
            ("auth", 1, 0),
            ("setup", 1, 0),
            ("malformed_output", 1, 0),
            ("timeout", 1, 0),
        ):
            with self.subTest(code=code):
                instances: list[object] = []

                class FailingReviewer:
                    def __init__(self, _profile: object) -> None:
                        instances.append(self)

                    def run_cover(self, _request, dispatched):
                        dispatched(100 + len(instances), 100 + len(instances), "synthetic")
                        raise reviewer_adapter.ContainedReviewerProcessError(
                            code, count=19, digest="b" * 64
                        )

                with (
                    mock.patch.object(reviewer_binding, "load_contained_reviewer_profile_bytes", return_value=object()),
                    mock.patch.object(reviewer_binding, "ContainedReviewerAdapter", FailingReviewer),
                    self.assertRaises(reviewer_adapter.ContainedReviewerProcessError) as caught,
                ):
                    reviewer_binding.run_cover_packet_reviewer(
                        b"profile", b"Inspect.", packet.document.encode(), schema.encode(), images
                    )
                self.assertEqual(len(instances), 1)

    def test_admission_fully_decodes_exact_rgba_png_preview(self) -> None:
        packet, packet_images = _packet_case(1)
        original = json.loads(packet.document)
        reference = original["candidates"][0]["reference"]
        schema = '{"additionalProperties":false,"properties":{},"type":"object"}'
        valid = packet_images[0][2]

        def bound(
            payload: bytes, *, preview_width: int = 1, preview_height: int = 1
        ) -> tuple[str, tuple[tuple[str, str, bytes], ...]]:
            value = json.loads(json.dumps(original))
            digest = hashlib.sha256(payload).hexdigest()
            source_bindings = value["candidates"][0]["source_bindings"]
            transport_reference = packet_builder.cover_transport_reference(
                reference, source_bindings, digest
            )
            value["candidates"][0].update({
                "preview_bytes": len(payload),
                "preview_height": preview_height,
                "preview_width": preview_width,
                "transport_reference": transport_reference,
                "transport_sha256": digest,
            })
            return _rebind_packet(value), ((transport_reference, digest, payload),)

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
        consecutive_idat = render([
            *canonical_chunks[:idat_index],
            (b"IDAT", idat_data[:split]),
            (b"IDAT", idat_data[split:]),
            *canonical_chunks[idat_index + 1:],
        ])
        trailing_idat_stream = render([
            *canonical_chunks[:idat_index],
            (b"IDAT", idat_data + b"trailing"),
            *canonical_chunks[idat_index + 1:],
        ])
        concatenated_idat_streams = render([
            *canonical_chunks[:idat_index],
            (b"IDAT", idat_data + idat_data),
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
        trns_before_idat = render([
            *canonical_chunks[:idat_index], (b"tRNS", b"\x00"),
            *canonical_chunks[idat_index:],
        ])
        gamma_after_idat = render([
            *canonical_chunks[:-1], (b"gAMA", struct.pack(">I", 45_455)),
            canonical_chunks[-1],
        ])

        icc_profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        ).tobytes()
        valid_icc = _preview(1, icc_profile=icc_profile)

        malformed = {
            "animated": _animated_preview(),
            "forged_signature": b"\x89PNG\r\n\x1a\nforged",
            "truncated": valid[:-4],
            "corrupt_crc": valid[:29] + bytes([valid[29] ^ 1]) + valid[30:],
            "trailing_bytes": valid + b"trailing",
            "wrong_mode": _preview(1, mode="RGB"),
            "decompression_failure": corrupt_idat(valid),
            "decompression_bomb_dimensions": oversized_header(valid),
            "nonconsecutive_idat": nonconsecutive_idat,
            "trailing_idat_stream": trailing_idat_stream,
            "concatenated_idat_streams": concatenated_idat_streams,
            "plte_after_idat": plte_after_idat,
            "duplicate_plte": duplicate_plte,
            "lowercase_reserved_bit": lowercase_reserved,
            "unknown_critical_chunk": unknown_critical,
            "ihdr_not_first": ihdr_not_first,
            "duplicate_ihdr": duplicate_ihdr,
            "iend_before_idat": iend_before_data,
            "trns_before_idat": trns_before_idat,
            "gamma_after_idat": gamma_after_idat,
            "icc_profile": valid_icc,
        }
        for name, payload in malformed.items():
            document, images = bound(payload)
            with self.subTest(case=name), self.assertRaisesRegex(
                Exception, "response_format"
            ):
                admit_cover_review_request("Inspect.", document, schema, images)

        document, images = bound(consecutive_idat)
        with self.assertRaisesRegex(Exception, "response_format"):
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

    def test_duplicate_group_aggregates_best_original_dimensions_canonically(self) -> None:
        duplicate_payload = _preview(31)
        other_payload = _preview(32)

        def embedded(
            token: str,
            kind: CoverSourceKind,
            payload: bytes,
            width: int,
            height: int,
        ) -> CoverSource:
            artifact = CoverArtifact.create(
                ("original-" + token).encode(), payload, "image/png", width, height
            )
            return CoverSource(
                cover_source_id(kind, token),
                kind,
                CoverSourceState.RETRIEVED,
                artifact,
                provenance=CoverProvenance(
                    kind.value,
                    CoverDesignation.WORK,
                    "work-" + token,
                ),
            )

        low = embedded(
            "duplicate-low", CoverSourceKind.HARDCOVER,
            duplicate_payload, 600, 900,
        )
        high = embedded(
            "duplicate-high", CoverSourceKind.OPEN_LIBRARY,
            duplicate_payload, 2000, 3000,
        )
        other = embedded(
            "separate", CoverSourceKind.HARDCOVER,
            other_payload, 1500, 3000,
        )
        by_artifact = {
            low.artifact.artifact_id: duplicate_payload,  # type: ignore[union-attr]
            high.artifact.artifact_id: duplicate_payload,  # type: ignore[union-attr]
            other.artifact.artifact_id: other_payload,  # type: ignore[union-attr]
        }
        documents = []
        for sources in ((low, high, other), (other, high, low), (high, low, other)):
            packet = prepare_cover_packet(
                _report(sources).to_json(),
                lambda artifact, _variant: by_artifact[artifact],
            )
            _admit_produced(packet)
            documents.append(packet.document)
        self.assertEqual(documents, [documents[0]] * 3)

        document = json.loads(documents[0])
        self.assertEqual(
            (document["candidates"][0]["width"], document["candidates"][0]["height"]),
            (2000, 3000),
        )
        self.assertEqual(
            (document["candidates"][1]["width"], document["candidates"][1]["height"]),
            (1500, 3000),
        )

    def test_digest_group_counts_every_source_kind_for_diversity(self) -> None:
        def candidate(
            index: int,
            members: tuple[tuple[CoverSourceKind, CoverDesignation | None], ...],
        ) -> packet_builder._Candidate:
            source_payload = _preview(index)
            source_digest = hashlib.sha256(source_payload).hexdigest()
            payload = packet_builder.normalize_cover_preview(
                source_payload, source_sha256=source_digest
            ).payload
            artifact = CoverArtifact.create(
                f"diversity-original-{index}".encode(),
                source_payload,
                "image/png",
                1000,
                1000,
            )
            sources: list[CoverSource] = []
            references: list[str] = []
            for member, (kind, designation) in enumerate(members):
                provenance = None
                if designation is not None:
                    provenance = CoverProvenance(
                        kind.value,
                        designation,
                        f"diversity-work-{index}-{member}",
                        (
                            f"diversity-edition-{index}-{member}"
                            if designation is CoverDesignation.EXACT_EDITION
                            else None
                        ),
                    )
                sources.append(CoverSource(
                    cover_source_id(kind, f"diversity-source-{index}-{member}"),
                    kind,
                    CoverSourceState.RETRIEVED,
                    artifact,
                    provenance=provenance,
                ))
                references.append("cover_ref_" + f"{index * 10 + member:064x}")
            return packet_builder._Candidate(
                reference=references[0],
                digest=hashlib.sha256(payload).hexdigest(),
                source_digest=source_digest,
                payload=payload,
                width=1000,
                height=1000,
                preview_width=1,
                preview_height=1,
                incumbent=members[0][0] is CoverSourceKind.ABS,
                sources=sources,
                references=references,
            )

        def candidates() -> list[packet_builder._Candidate]:
            values = [candidate(1, (
                (CoverSourceKind.ABS, None),
                (CoverSourceKind.OPEN_LIBRARY, CoverDesignation.WORK),
            ))]
            values.extend(
                candidate(index, ((CoverSourceKind.HARDCOVER, CoverDesignation.EXACT_EDITION),))
                for index in range(2, 5)
            )
            values.extend(
                candidate(index, ((CoverSourceKind.OPEN_LIBRARY, CoverDesignation.EXACT_EDITION),))
                for index in range(5, 8)
            )
            values.extend((
                candidate(8, ((CoverSourceKind.HARDCOVER, CoverDesignation.DEFAULT),)),
                candidate(9, ((CoverSourceKind.OPEN_LIBRARY, CoverDesignation.WORK),)),
            ))
            return values

        documents = []
        for permutation in (
            candidates(),
            list(reversed(candidates())),
            candidates()[3:] + candidates()[:3],
        ):
            packet = packet_builder._finalize_cover_packet(permutation, [])
            _admit_produced(packet)
            documents.append(packet.document)
        self.assertEqual(documents, [documents[0]] * 3)

        document = json.loads(documents[0])
        incumbent_kinds = {
            value["source_kind"]
            for value in document["candidates"][0]["provenance"]
        }
        self.assertEqual(incumbent_kinds, {"abs", "open_library"})
        self.assertEqual(len(document["candidates"]), 9)
        selected_kinds = {
            value["source_kind"]
            for candidate in document["candidates"]
            for value in candidate["provenance"]
        }
        self.assertEqual(selected_kinds, {"abs", "hardcover", "open_library"})

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
            [value["transport_reference"] for value in packet["candidates"]],
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
        admitted = admit_cover_review_request(
            "Inspect.", over_bytes_document, schema, over_bytes_images
        )
        self.assertEqual(len(admitted.images), 9)
        self.assertLessEqual(sum(len(value[2]) for value in over_bytes_images), 8_388_608)
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

        reachable = ((packet.document, images, {
                "preview_bytes": 1_048_577,
                "reason": "preview_byte_limit",
                "reference": omitted_reference,
            }),)
        for base, bound_images, omission in reachable:
            admit_cover_review_request(
                "Inspect.", document_with(base, omission), schema, bound_images
            )

        duplicate_packet = packet_builder._finalize_cover_packet(
            [_group_candidate(1, duplicates=2)], []
        )
        _admit_produced(duplicate_packet)
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

    def test_duplicate_source_bindings_require_a_complete_omission_audit(self) -> None:
        packet = packet_builder._finalize_cover_packet(
            [_group_candidate(1, duplicates=2)], []
        )
        document = json.loads(packet.document)
        document["omissions"] = []
        images = tuple(
            (value.reference, value.digest, value.payload)
            for value in packet.images
        )

        with self.assertRaisesRegex(Exception, "response_format"):
            admit_cover_review_request(
                "Inspect.",
                _rebind_packet(document),
                '{"additionalProperties":false,"properties":{},"type":"object"}',
                images,
            )

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
        retained = [_group_candidate(index) for index in range(1, 9)]
        packet = packet_builder._finalize_cover_packet(
            [*retained, _group_candidate(20, duplicates=2, width=1, height=1)],
            [],
        )
        _admit_produced(packet)
        document = json.loads(packet.document)
        self.assertEqual(len(document["candidates"]), 9)
        self.assertEqual(
            [value["reason"] for value in document["omissions"]],
            ["duplicate_preview"],
        )
        self.assertLessEqual(
            sum(value["preview_bytes"] for value in document["candidates"]),
            8_388_608,
        )

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


class CoverObservationTests(unittest.TestCase):
    def test_every_closed_value_is_admitted_without_policy_projection(self) -> None:
        packet, images = _packet_case(5)

        def all_values(value: dict[str, object]) -> None:
            observations = value["observations"]
            assert isinstance(observations, list)
            media = ("photographic", "illustrated", "typographic", "mixed", "unknown")
            for index, observation in enumerate(observations):
                observation["medium"] = media[index]
                observation["composition"] = ("minimal", "dense", "unknown")[index % 3]
                observation["promotional_text"] = ("present", "absent", "unknown")[index % 3]
                observation["tie_in_branding"] = ("absent", "unknown", "present")[index % 3]

        report = observe_cover_packet(
            b"synthetic-profile",
            packet.document.encode(),
            images,
            lambda *args: _observer_result(*args, mutate=all_values),
        )
        observations = json.loads(report.document)["observations"]
        self.assertEqual(
            {value["medium"] for value in observations},
            {"photographic", "illustrated", "typographic", "mixed", "unknown"},
        )
        self.assertTrue(
            all(
                set(value)
                == {
                    "candidate_reference",
                    "composition",
                    "medium",
                    "preview_sha256",
                    "preview_variant",
                    "promotional_text",
                    "tie_in_branding",
                }
                for value in observations
            )
        )

    def test_closed_observations_bind_every_exact_preview_and_hide_source_values(self) -> None:
        packet, images = _packet_case(3)
        profile = b"synthetic-profile"
        instructions: list[str] = []

        def reviewer(*args: object) -> str:
            instruction = args[1]
            assert isinstance(instruction, bytes)
            instructions.append(instruction.decode())
            return _observer_result(*args)

        report = observe_cover_packet(profile, packet.document.encode(), images, reviewer)
        value = json.loads(report.document)

        self.assertIn(TAXONOMY_DOCUMENT, instructions[0])
        self.assertIn(
            "a single dominant image or figure with a simple supporting layout is minimal",
            instructions[0],
        )
        self.assertTrue(
            isinstance(TAXONOMY, tuple)
            and all(
                isinstance(values, tuple)
                and all(isinstance(item, tuple) for item in values)
                for _name, values in TAXONOMY
            )
        )
        self.assertEqual(value["schema_version"], OBSERVATION_SCHEMA_VERSION)
        self.assertEqual(value["taxonomy_reference"], TAXONOMY_REFERENCE)
        self.assertEqual(value["cover_packet_reference"], json.loads(packet.document)["packet_reference"])
        self.assertEqual(len(value["observations"]), 3)
        packet_candidates = {
            item["reference"]: item for item in json.loads(packet.document)["candidates"]
        }
        for observation in value["observations"]:
            candidate = packet_candidates[observation["candidate_reference"]]
            self.assertEqual(observation["preview_sha256"], candidate["preview_sha256"])
            self.assertEqual(observation["preview_variant"], candidate["preview_variant"])
            self.assertIn(observation["composition"], {"minimal", "dense", "unknown"})
        self.assertNotIn("Synthetic title", report.document)
        self.assertNotIn("source-", report.document)
        self.assertRegex(value["observer_reference"], r"^cover_observer_[0-9a-f]{64}$")

    def test_invented_duplicate_hostile_and_unbound_results_fail_closed(self) -> None:
        packet, images = _packet_case()
        profile = b"synthetic-profile"

        def invented(value: dict[str, object]) -> None:
            observations = value["observations"]
            assert isinstance(observations, list)
            observations[0]["candidate_reference"] = "cover_ref_" + "f" * 64

        def duplicate(value: dict[str, object]) -> None:
            observations = value["observations"]
            assert isinstance(observations, list)
            observations[1]["candidate_reference"] = observations[0]["candidate_reference"]

        def hostile(value: dict[str, object]) -> None:
            observations = value["observations"]
            assert isinstance(observations, list)
            observations[0]["medium"] = "/private/hostile-value"

        for mutation in (invented, duplicate, hostile):
            with self.subTest(mutation=mutation.__name__), self.assertRaises(ValueError):
                observe_cover_packet(
                    profile,
                    packet.document.encode(),
                    images,
                    lambda *args, mutation=mutation: _observer_result(
                        *args, mutate=mutation
                    ),
                )

        def unbound(*args: object) -> str:
            value = json.loads(_observer_result(*args))
            value["input_sha256"] = "0" * 64
            return canonical_json(value)

        with self.assertRaisesRegex(ValueError, "binding"):
            observe_cover_packet(profile, packet.document.encode(), images, unbound)

    def test_observation_report_persists_only_bound_retry_evidence(self) -> None:
        packet, images = _packet_case()

        def retried(*args: object) -> str:
            envelope = json.loads(_observer_result(*args))
            envelope["attempt_evidence"] = [
                {
                    "diagnostic_bytes": 17,
                    "diagnostic_sha256": "a" * 64,
                    "dispatched": True,
                    "failure_code": "local_cli_abort",
                },
                {"dispatched": True},
            ]
            return canonical_json(envelope)

        report = json.loads(observe_cover_packet(
            b"synthetic-profile", packet.document.encode(), images, retried
        ).document)
        self.assertEqual(report["attempt_evidence"], [
            {
                "diagnostic_bytes": 17,
                "diagnostic_sha256": "a" * 64,
                "dispatched": True,
                "failure_code": "local_cli_abort",
            },
            {"dispatched": True},
        ])

    def test_typed_scenario_uses_only_closed_inputs_and_redacts_failure(self) -> None:
        payloads = tuple(_preview(index) for index in range(1, 3))
        sources = tuple(
            _source(index, payload) for index, payload in enumerate(payloads, start=1)
        )
        report = _report(sources)
        by_artifact = {
            source.artifact.artifact_id: payload
            for source, payload in zip(sources, payloads, strict=True)
            if source.artifact is not None
        }
        packet = prepare_cover_packet(
            report.to_json(), lambda artifact, _variant: by_artifact[artifact]
        )

        def operation(*_args: object) -> str:
            return '{"ok":true}'

        operations = WorkbenchOperations(
            *(operation for _ in WorkbenchOperations.__dataclass_fields__)
        )
        operations = replace(
            operations,
            reopen_metadata_report=lambda *_: report.to_json(),
            reopen_cover_preview=(
                lambda _db, _artifacts, _report, artifact, _variant:
                by_artifact[artifact]
            ),
            run_cover_packet_reviewer=_observer_result,
        )
        with WorkbenchContext.temporary() as context:
            profile = context.path("contained-reviewer-profile.json")
            profile.write_bytes(b"synthetic-profile")
            profile.chmod(0o600)
            context.network = IsolatedNetworkProfile(
                "isolated-test",
                True,
                True,
                tuple(
                    sorted(
                        {
                            hashlib.sha256(b"synthetic-profile").hexdigest(),
                            packet.packet_digest,
                        }
                    )
                ),
            )
            result = execute(
                context, CoverObservationRequest(report.report_id), operations
            )
            document = json.loads(
                context.path("artifacts/" + result.artifacts[0]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result.name, "cover-observation-report")
            self.assertEqual(len(document["observations"]), 2)

            invalid = replace(
                operations,
                run_cover_packet_reviewer=(
                    lambda *_: '{"private":"/untrusted/private/value"}'
                ),
            )
            with self.assertRaises(WorkbenchFailure) as caught:
                execute(context, CoverObservationRequest(report.report_id), invalid)
            self.assertEqual(caught.exception.category, "invalid")
            self.assertNotIn("private", str(caught.exception))


class CoverIdentityTests(unittest.TestCase):
    def test_identity_reauthenticates_every_source_to_the_normalized_transport(self) -> None:
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        ).tobytes()
        plain = _preview(40, width=7, height=9)
        profiled = _preview(40, width=7, height=9, icc_profile=profile)
        sources = (
            _source(40, plain, embedded=True),
            _source(41, profiled, embedded=True),
        )
        report = _report(sources)
        payload_by_artifact = {
            source.artifact.artifact_id: payload
            for source, payload in zip(sources, (plain, profiled), strict=True)
            if source.artifact is not None
        }
        packet = prepare_cover_packet(
            report.to_json(),
            lambda artifact, _variant: payload_by_artifact[artifact],
        )
        images = tuple(
            (value.reference, value.digest, value.payload)
            for value in packet.images
        )
        result = json.loads(
            classify_cover_identity(
                report.to_json(),
                packet.document,
                images,
                read_preview=lambda artifact, _variant: payload_by_artifact[artifact],
            ).document
        )
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(
            result["candidates"][0]["outcome"],
            "verified_manifestation_match",
        )

        different_payloads = (_preview(50), _preview(51))
        different_sources = (
            _source(50, different_payloads[0], embedded=True),
            _source(51, different_payloads[1], embedded=True),
        )
        different_report = _report(different_sources)
        different_by_artifact = {
            source.artifact.artifact_id: payload
            for source, payload in zip(
                different_sources, different_payloads, strict=True
            )
            if source.artifact is not None
        }
        separate = prepare_cover_packet(
            different_report.to_json(),
            lambda artifact, _variant: different_by_artifact[artifact],
        )
        separate_document = json.loads(separate.document)
        ordered_sources = different_report.cover_sources
        representative_reference = packet_builder.cover_source_reference(
            different_report.report_id, ordered_sources[0].source_id
        )
        representative = next(
            value
            for value in separate_document["candidates"]
            if value["reference"] == representative_reference
        )
        other_source = ordered_sources[1]
        assert other_source.artifact is not None
        other_reference = packet_builder.cover_source_reference(
            different_report.report_id, other_source.source_id
        )
        representative["source_bindings"] = sorted([
            *representative["source_bindings"],
            {
                "preview_sha256": other_source.artifact.preview_digest,
                "reference": other_reference,
            },
        ], key=lambda value: (value["reference"], value["preview_sha256"]))
        representative["width"] = max(
            source.artifact.width
            for source in ordered_sources
            if source.artifact is not None
        )
        representative["height"] = max(
            source.artifact.height
            for source in ordered_sources
            if source.artifact is not None
        )
        representative["transport_reference"] = (
            packet_builder.cover_transport_reference(
                representative["reference"],
                representative["source_bindings"],
                representative["transport_sha256"],
            )
        )
        separate_document["candidates"] = [representative]
        separate_document["omissions"] = [{
            "preview_sha256": other_source.artifact.preview_digest,
            "reason": "duplicate_preview",
            "reference": other_reference,
            "retained_reference": representative_reference,
        }]
        original_image = next(
            value
            for value in separate.images
            if value.digest == representative["transport_sha256"]
        )
        forged_images = ((
            representative["transport_reference"],
            original_image.digest,
            original_image.payload,
        ),)

        with self.assertRaisesRegex(ValueError, "transport binding"):
            classify_cover_identity(
                different_report.to_json(),
                _rebind_packet(separate_document),
                forged_images,
                read_preview=(
                    lambda artifact, _variant: different_by_artifact[artifact]
                ),
            )

    def _typed_discovery_identity_case(
        self,
        *,
        local_creators: tuple[str, ...],
        provider_creators: tuple[str, ...],
        lane_kind: ProviderQueryKind | None = ProviderQueryKind.ISBN,
        unrelated_isbn_mapping: bool = False,
    ) -> tuple[str, str, tuple[tuple[str, str, bytes], ...], bytes]:
        payload = _preview(33)
        source = _source(3, payload)
        local_claims = (
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Synthetic title",
                canonical="Synthetic title",
                excerpt="Synthetic title",
                applicability="not_applicable",
                basis="target_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
            ),
            *tuple(
                LocalClaim.create(
                    kind=ClaimKind.CREATOR,
                    observed=value,
                    canonical=value,
                    excerpt=value,
                    applicability="not_applicable",
                    checksum="not_applicable",
                    applicability_basis="not_applicable",
                    role="author",
                    occurrence=index,
                )
                for index, value in enumerate(local_creators)
            ),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780306406157",
                canonical="9780306406157",
                excerpt="ISBN 9780306406157",
                applicability="unknown",
                checksum="valid_isbn_13",
                applicability_basis="unlabelled",
            ),
        )
        claims = (
            ProviderClaim.create(
                provider="hardcover",
                candidate="edition-3",
                kind=ClaimKind.TITLE,
                value="Synthetic title",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            *tuple(
                ProviderClaim.create(
                    provider="hardcover",
                    candidate="edition-3",
                    kind=ClaimKind.CREATOR,
                    value=value,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    role="author",
                    raw_order=index + 1,
                )
                for index, value in enumerate(provider_creators)
            ),
        )
        candidate = ProviderCandidate(
            "edition-3",
            "work-3",
            claims,
        )
        candidates = (candidate,)
        lanes: tuple[ProviderLaneObservation, ...] = ()
        input_references: tuple[str, ...] = ()
        if lane_kind is not None:
            mapped_candidate = candidate
            if unrelated_isbn_mapping:
                mapped_candidate = ProviderCandidate(
                    "edition-4",
                    "work-4",
                    (),
                )
                candidates = (candidate, mapped_candidate)
            isbn_identity = "lane_" + "a" * 64
            work_identity = "lane_" + "b" * 64
            isbn_candidate = (
                mapped_candidate
                if lane_kind is ProviderQueryKind.ISBN or unrelated_isbn_mapping
                else None
            )
            work_candidate = candidate if lane_kind is ProviderQueryKind.WORK else None
            def lane(
                identity: str,
                kind: ProviderQueryKind,
                item: ProviderCandidate | None,
                inputs: tuple[str, ...],
            ) -> ProviderLaneObservation:
                return ProviderLaneObservation(
                    identity,
                    kind,
                    ProviderStatus.CANDIDATES if item is not None else ProviderStatus.NO_MATCH,
                    inputs,
                    "9780306406157" if kind is ProviderQueryKind.ISBN else None,
                    () if item is None else (
                        ProviderLaneCandidate(
                            provider_lane_candidate_reference(
                                "hardcover", identity, item.identity, item.work_identity
                            ),
                            item.identity,
                            item.work_identity,
                        ),
                    ),
                )
            lanes = (
                lane(isbn_identity, ProviderQueryKind.ISBN, isbn_candidate, (local_claims[-1].reference,)),
                lane(work_identity, ProviderQueryKind.WORK, work_candidate, (local_claims[0].reference,)),
            )
            input_references = tuple(sorted({reference for lane_value in lanes for reference in lane_value.input_references}))
        report = MetadataReport.create(
            target=TargetFacts(
                "synthetic", "books", "item", default_target_fields("Synthetic title")
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=local_claims,
            local_reasons=("package_metadata_creator",),
            providers=(
                ProviderObservation(
                    "hardcover",
                    ProviderStatus.CANDIDATES,
                    input_references,
                    candidates,
                    ProviderQueryKind.DISCOVERY,
                    "hardcover",
                    lanes=lanes,
                ),
            ),
            cover_sources=(source,),
        )
        packet = prepare_cover_packet(report.to_json(), lambda *_: payload)
        images = tuple(
            (item.reference, item.digest, item.payload) for item in packet.images
        )
        return report.to_json(), packet.document, images, payload

    def test_typed_discovery_creator_evidence_uses_any_agreement_over_pairwise_noise(self) -> None:
        for local_creators, provider_creators, expected in (
            (("Ada Author", "Bea Author"), ("Ada Author", "Bea Author"), "plausible_match"),
            (("Ada Author", "Bea Author"), ("Ada Author",), "plausible_match"),
            (("Ada Author",), ("Different Author",), "unknown"),
            (("Ada Author",), (), "plausible_match"),
        ):
            with self.subTest(local_creators=local_creators, provider_creators=provider_creators):
                report, packet, images, payload = self._typed_discovery_identity_case(
                    local_creators=local_creators,
                    provider_creators=provider_creators,
                )
                result = json.loads(
                    _classify_identity(
                        report, packet, images, lambda *_: payload
                    ).document
                )
                self.assertEqual(result["candidates"][0]["outcome"], expected)

    def test_typed_lane_mapping_requires_the_matched_candidate_in_an_isbn_lane(self) -> None:
        cases = (
            (ProviderQueryKind.WORK, False, "unknown"),
            (ProviderQueryKind.ISBN, False, "plausible_match"),
            (ProviderQueryKind.WORK, True, "unknown"),
            (None, False, "plausible_match"),
        )
        for lane_kind, unrelated_isbn_mapping, expected in cases:
            with self.subTest(
                lane_kind=lane_kind,
                unrelated_isbn_mapping=unrelated_isbn_mapping,
            ):
                report, packet, images, payload = self._typed_discovery_identity_case(
                    local_creators=(),
                    provider_creators=(),
                    lane_kind=lane_kind,
                    unrelated_isbn_mapping=unrelated_isbn_mapping,
                )
                value = json.loads(
                    _classify_identity(
                        report, packet, images, lambda *_: payload
                    ).document
                )
                self.assertEqual(value["candidates"][0]["outcome"], expected)

    def test_source_bound_epub_cover_is_verified_manifestation(self) -> None:
        source = _source(3, _preview(3), embedded=True)
        self.assertEqual(
            _classify_sources(
                SimpleNamespace(providers=(), identifier_evidence=(), local_claims=()),
                (source,),
            ),
            ("verified_manifestation_match", ["source_bound_epub_cover"], []),
        )

        incumbent = _source(4, _preview(4), incumbent=True)
        self.assertEqual(
            _classify_sources(
                SimpleNamespace(providers=(), identifier_evidence=(), local_claims=()),
                (incumbent,),
            ),
            ("unknown", ["no_provider_identity"], []),
        )

    def test_title_supported_provider_discovery_is_plausible_despite_edition_noise(self) -> None:
        source = _source(3, _preview(3))
        assert source.provenance is not None
        agreement = SimpleNamespace(outcome=Comparison.AGREEMENT)
        disagreement = SimpleNamespace(outcome=Comparison.DISAGREEMENT)
        candidate = SimpleNamespace(
            identity=source.provenance.edition_identity,
            work_identity=source.provenance.work_identity,
            claims=(
                SimpleNamespace(kind=ClaimKind.TITLE, links=(agreement, disagreement)),
                SimpleNamespace(kind=ClaimKind.LANGUAGE, links=(disagreement,)),
                SimpleNamespace(kind=ClaimKind.PUBLISHER, links=(disagreement,)),
            ),
            evidence=(
                SimpleNamespace(
                    kind=ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                    links=(disagreement,),
                ),
            ),
        )
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(candidate,),
                    query_kind=ProviderQueryKind.DISCOVERY,
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(
            _classify_sources(report, (source,)),
            (
                "plausible_match",
                [
                    "provider_identity_without_explicit_identifier",
                    "retained_title_agreement",
                ],
                [],
            ),
        )
        work_report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(candidate,),
                    query_kind=ProviderQueryKind.WORK,
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(
            _classify_sources(work_report, (source,)),
            ("unknown", ["retained_title_evidence_insufficient"], []),
        )

    def test_explicit_creator_disagreement_vetoes_title_supported_plausible_match(self) -> None:
        source = _source(3, _preview(3))
        assert source.provenance is not None
        agreement = SimpleNamespace(outcome=Comparison.AGREEMENT)
        disagreement = SimpleNamespace(outcome=Comparison.DISAGREEMENT)
        candidate = SimpleNamespace(
            identity=source.provenance.edition_identity,
            work_identity=source.provenance.work_identity,
            claims=(
                SimpleNamespace(kind=ClaimKind.TITLE, links=(agreement,)),
                SimpleNamespace(kind=ClaimKind.CREATOR, links=(disagreement,)),
                SimpleNamespace(kind=ClaimKind.CONTRIBUTOR, links=(disagreement,)),
            ),
            evidence=(
                SimpleNamespace(
                    kind=ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                    links=(disagreement,),
                ),
            ),
        )
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(candidate,),
                    query_kind=ProviderQueryKind.DISCOVERY,
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(
            _classify_sources(report, (source,)),
            ("unknown", ["retained_creator_or_contributor_disagreement"], []),
        )

    def test_creator_or_contributor_evidence_agreement_defeats_disagreement_noise(self) -> None:
        source = _source(3, _preview(3))
        assert source.provenance is not None
        agreement = SimpleNamespace(outcome=Comparison.AGREEMENT)
        disagreement = SimpleNamespace(outcome=Comparison.DISAGREEMENT)
        candidate = SimpleNamespace(
            identity=source.provenance.edition_identity,
            work_identity=source.provenance.work_identity,
            claims=(SimpleNamespace(kind=ClaimKind.TITLE, links=(agreement,)),),
            evidence=(
                SimpleNamespace(
                    kind=ProviderEvidenceKind.AUTHOR_IDENTITY,
                    links=(agreement, disagreement),
                ),
                SimpleNamespace(
                    kind=ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                    links=(disagreement,),
                ),
            ),
        )
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(candidate,),
                    query_kind=ProviderQueryKind.DISCOVERY,
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(_classify_sources(report, (source,))[0], "plausible_match")

    def test_missing_creator_is_neutral_for_title_supported_plausible_match(self) -> None:
        source = _source(3, _preview(3))
        assert source.provenance is not None
        agreement = SimpleNamespace(outcome=Comparison.AGREEMENT)
        candidate = SimpleNamespace(
            identity=source.provenance.edition_identity,
            work_identity=source.provenance.work_identity,
            claims=(SimpleNamespace(kind=ClaimKind.TITLE, links=(agreement,)),),
            evidence=(),
        )
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(candidate,),
                    query_kind=ProviderQueryKind.DISCOVERY,
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(_classify_sources(report, (source,))[0], "plausible_match")

    def test_every_outcome_is_closed_and_does_not_project_hostile_values(self) -> None:
        payload = _preview(3)
        source = _source(3, payload)
        assert source.provenance is not None
        candidate = SimpleNamespace(
            identity=source.provenance.edition_identity,
            work_identity=source.provenance.work_identity,
            claims=(),
            evidence=(),
        )
        accepted = SimpleNamespace(
            provider=source.provenance.provider,
            candidate_identity=candidate.identity,
            work_identity=candidate.work_identity,
            relation=IdentifierRelation.ACCEPTED,
            reference="provider_" + "a" * 64,
        )
        report = SimpleNamespace(
            providers=(SimpleNamespace(provider=source.provenance.provider, candidates=(candidate,)),),
            identifier_evidence=(accepted,),
            local_claims=(),
        )
        with mock.patch(
            "mechanical_librarian_workbench.cover_identity.manifestation_publication_compatible",
            return_value=True,
        ):
            self.assertEqual(
                _classify_sources(report, (source,))[0],
                "verified_manifestation_match",
            )
        work_source = replace(
            source,
            provenance=replace(source.provenance, designation=CoverDesignation.WORK, edition_identity=None),
        )
        associated = SimpleNamespace(**{**accepted.__dict__, "relation": IdentifierRelation.ASSOCIATED})
        self.assertEqual(_classify_sources(SimpleNamespace(providers=report.providers, identifier_evidence=(associated,)), (work_source,))[0], "verified_work_match")
        default_source = replace(
            work_source,
            provenance=replace(
                work_source.provenance, designation=CoverDesignation.DEFAULT
            ),
        )
        self.assertEqual(
            _classify_sources(
                SimpleNamespace(
                    providers=report.providers,
                    identifier_evidence=(associated,),
                ),
                (default_source,),
            )[0],
            "verified_work_match",
        )
        self.assertEqual(_classify_sources(SimpleNamespace(providers=report.providers, identifier_evidence=()), (source,))[0], "unknown")
        self.assertEqual(_classify_sources(SimpleNamespace(providers=(), identifier_evidence=()), (source,))[0], "unknown")
        contradiction = SimpleNamespace(kind=ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION)
        conflicting = SimpleNamespace(**{**candidate.__dict__, "evidence": (contradiction,)})
        self.assertEqual(_classify_sources(SimpleNamespace(providers=(SimpleNamespace(provider=source.provenance.provider, candidates=(conflicting,)),), identifier_evidence=()), (source,))[0], "conflict")

    def test_deterministic_contradictions_and_title_supported_discovery(self) -> None:
        source = _source(3, _preview(3))
        assert source.provenance is not None
        candidate = SimpleNamespace(
            identity=source.provenance.edition_identity,
            work_identity=source.provenance.work_identity,
            claims=(),
            evidence=(),
        )
        identifier = SimpleNamespace(
            provider=source.provenance.provider,
            candidate_identity=candidate.identity,
            work_identity=candidate.work_identity,
            relation=IdentifierRelation.UNRELATED,
            reference="provider_" + "a" * 64,
        )
        provider = SimpleNamespace(
            provider=source.provenance.provider,
            candidates=(candidate,),
        )
        report = SimpleNamespace(
            providers=(provider,), identifier_evidence=(identifier,), local_claims=()
        )
        self.assertEqual(
            _classify_sources(report, (source,)),
            ("conflict", ["unrelated_identifier"], [identifier.reference]),
        )

        disagreement = SimpleNamespace(
            outcome=Comparison.DISAGREEMENT,
            local_reference="local_" + "b" * 64,
        )
        mixed_agreement = SimpleNamespace(
            outcome=Comparison.AGREEMENT,
            local_reference="local_" + "f" * 64,
        )
        title = SimpleNamespace(
            kind=ClaimKind.TITLE,
            links=(disagreement, mixed_agreement),
            reference="provider_" + "c" * 64,
        )
        conflicting = SimpleNamespace(
            **{**candidate.__dict__, "claims": (title,)}
        )
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(conflicting,),
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(
            _classify_sources(report, (source,)),
            (
                "plausible_match",
                [
                    "provider_identity_without_explicit_identifier",
                    "retained_title_agreement",
                ],
                [],
            ),
        )

        date_disagreement = SimpleNamespace(
            outcome=Comparison.DISAGREEMENT,
            local_reference="local_" + "9" * 64,
        )
        date_evidence = SimpleNamespace(
            kind=ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            links=(date_disagreement,),
        )
        conflicting_date = SimpleNamespace(
            **{
                **candidate.__dict__,
                "claims": (
                    SimpleNamespace(
                        kind=ClaimKind.TITLE,
                        links=(mixed_agreement,),
                        reference="provider_" + "8" * 64,
                    ),
                ),
                "evidence": (date_evidence,),
            }
        )
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(conflicting_date,),
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(
            _classify_sources(report, (source,)),
            (
                "plausible_match",
                [
                    "provider_identity_without_explicit_identifier",
                    "retained_title_agreement",
                ],
                [],
            ),
        )

        agreement = SimpleNamespace(
            outcome=Comparison.AGREEMENT,
            local_reference="local_" + "d" * 64,
        )
        agreeing = SimpleNamespace(
            **{
                **candidate.__dict__,
                "claims": (
                    SimpleNamespace(
                        kind=ClaimKind.TITLE,
                        links=(agreement,),
                        reference="provider_" + "e" * 64,
                    ),
                ),
            }
        )
        report = SimpleNamespace(
            providers=(
                SimpleNamespace(
                    provider=source.provenance.provider,
                    candidates=(agreeing,),
                ),
            ),
            identifier_evidence=(),
            local_claims=(),
        )
        self.assertEqual(
            _classify_sources(report, (source,)),
            (
                "plausible_match",
                [
                    "provider_identity_without_explicit_identifier",
                    "retained_title_agreement",
                ],
                [],
            ),
        )

        accepted = SimpleNamespace(
            **{**identifier.__dict__, "relation": IdentifierRelation.ACCEPTED}
        )
        report = SimpleNamespace(
            providers=(provider,), identifier_evidence=(accepted,), local_claims=()
        )
        with mock.patch(
            "mechanical_librarian_workbench.cover_identity.manifestation_publication_compatible",
            return_value=False,
        ):
            self.assertEqual(
                _classify_sources(report, (source,)),
                (
                    "conflict",
                    ["manifestation_publication_incompatible"],
                    [accepted.reference],
                ),
            )

    def test_report_binds_exact_packet_candidates_and_emits_only_opaque_references(self) -> None:
        payloads = tuple(_preview(index) for index in range(1, 3))
        sources = tuple(
            _source(index, payload)
            for index, payload in enumerate(payloads, start=1)
        )
        report_document = _report(sources).to_json()
        report = MetadataReport.from_json(report_document)
        by_artifact = {
            source.artifact.artifact_id: payload
            for source, payload in zip(sources, payloads, strict=True)
            if source.artifact is not None
        }
        packet = prepare_cover_packet(
            report_document, lambda artifact, _variant: by_artifact[artifact]
        )
        images = tuple(
            (item.reference, item.digest, item.payload)
            for item in packet.images
        )
        with mock.patch("mechanical_librarian_workbench.cover_identity.MetadataReport.from_json", return_value=report):
            document = _classify_identity(
                report_document,
                packet.document,
                images,
                lambda artifact, _variant: by_artifact[artifact],
            ).document
        value = json.loads(document)
        self.assertEqual(value["schema_version"], SCHEMA_VERSION)
        self.assertEqual(value["cover_packet_reference"], json.loads(packet.document)["packet_reference"])
        self.assertTrue(value["work_target_reference"].startswith("work_target_ref_"))
        self.assertEqual(len(value["candidates"]), 2)
        self.assertTrue(all(row["outcome"] == "unknown" for row in value["candidates"]))
        self.assertTrue(
            all(
                row["preview_reference"].startswith("cover_preview_ref_")
                for row in value["candidates"]
            )
        )
        self.assertNotIn("Synthetic title", document)
        self.assertNotIn("source-", document)

        epub_payload = _preview(8)
        epub_source = _source(8, epub_payload, embedded=True)
        epub_report = _report((epub_source,))
        epub_packet = prepare_cover_packet(
            epub_report.to_json(), lambda *_: epub_payload
        )
        epub_images = tuple(
            (item.reference, item.digest, item.payload)
            for item in epub_packet.images
        )
        epub_document = json.loads(
            _classify_identity(
                epub_report.to_json(),
                epub_packet.document,
                epub_images,
                lambda *_: epub_payload,
            ).document
        )
        self.assertEqual(
            epub_document["candidates"][0]["outcome"],
            "verified_manifestation_match",
        )
        self.assertEqual(
            epub_document["candidates"][0]["reasons"],
            ["source_bound_epub_cover"],
        )

        shared_payload = _preview(9)
        foreign_source = _source(9, shared_payload)
        current_source = _source(10, shared_payload)
        foreign_report = _report((foreign_source,))
        current_report = _report((current_source,))
        assert foreign_source.artifact is not None
        foreign_packet = prepare_cover_packet(
            foreign_report.to_json(), lambda *_: shared_payload
        )
        foreign_images = tuple(
            (item.reference, item.digest, item.payload)
            for item in foreign_packet.images
        )
        with self.assertRaisesRegex(ValueError, "report binding"):
            classify_cover_identity(
                current_report.to_json(),
                foreign_packet.document,
                foreign_images,
                read_preview=lambda *_: shared_payload,
            )

    def test_typed_scenario_executes_and_redacts_invalid_report(self) -> None:
        payloads = tuple(_preview(index) for index in range(1, 3))
        sources = tuple(
            _source(index, payload)
            for index, payload in enumerate(payloads, start=1)
        )
        report = _report(sources)
        by_artifact = {
            source.artifact.artifact_id: payload
            for source, payload in zip(sources, payloads, strict=True)
            if source.artifact is not None
        }

        def operation(*_args: object) -> str:
            return '{"ok":true}'

        operations = WorkbenchOperations(
            *(operation for _ in WorkbenchOperations.__dataclass_fields__)
        )
        operations = replace(
            operations,
            reopen_metadata_report=lambda *_: report.to_json(),
            reopen_cover_preview=(
                lambda _db, _artifacts, _report, artifact, _variant:
                by_artifact[artifact]
            ),
        )
        with WorkbenchContext.temporary() as context:
            result = execute(
                context, CoverIdentityRequest(report.report_id), operations
            )
            document = json.loads(
                context.path("artifacts/" + result.artifacts[0]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result.name, "cover-identity-report")
            self.assertEqual(len(document["candidates"]), 2)

            invalid = replace(
                operations,
                reopen_metadata_report=(
                    lambda *_: '{"private":"/untrusted/private/value"}'
                ),
            )
            with self.assertRaises(WorkbenchFailure) as caught:
                execute(context, CoverIdentityRequest(report.report_id), invalid)
            self.assertEqual(caught.exception.category, "invalid")
            self.assertNotIn("private", str(caught.exception))


def _policy_documents(count: int = 3):
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
    packet_value = json.loads(packet.document)
    candidates = packet_value["candidates"]
    identity = {
        "candidates": [
            {
                "candidate_reference": candidate["reference"],
                "evidence_references": [],
                "outcome": "verified_work_match",
                "preview_reference": _preview_reference(
                    packet_value["packet_reference"], candidate
                ),
                "reasons": ["work_identity_verified"],
            }
            for candidate in candidates
        ],
        "classifier_reference": _CLASSIFIER_REFERENCE,
        "cover_packet_reference": packet_value["packet_reference"],
        "report_reference": "report_" + "a" * 64,
        "schema_version": SCHEMA_VERSION,
        "work_target_reference": "work_target_ref_" + "b" * 64,
    }
    references = tuple(candidate["reference"] for candidate in candidates)
    observation = {
        "attempt_evidence": [{"dispatched": True}],
        "cover_packet_reference": packet_value["packet_reference"],
        "instruction_sha256": hashlib.sha256(INSTRUCTION.encode()).hexdigest(),
        "observations": [
            {
                "candidate_reference": candidate["reference"],
                "composition": "dense",
                "medium": "illustrated",
                "preview_sha256": candidate["preview_sha256"],
                "preview_variant": candidate["preview_variant"],
                "promotional_text": "absent",
                "tie_in_branding": "absent",
            }
            for candidate in candidates
        ],
        "observer_reference": "cover_observer_" + "c" * 64,
        "output_schema_sha256": hashlib.sha256(
            _output_schema(references).encode()
        ).hexdigest(),
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "taxonomy_reference": TAXONOMY_REFERENCE,
    }
    return (
        report.to_json(),
        packet,
        images,
        identity,
        observation,
        references,
        lambda artifact, _variant: by_artifact[artifact],
    )


class CoverPolicyTests(unittest.TestCase):
    def _adjudicate(
        self,
        policy: CoverPolicy = CoverPolicy(),
        *,
        identity_mutation: object | None = None,
        observation_mutation: object | None = None,
        observation_available: bool = True,
    ) -> dict[str, object]:
        (
            report_document,
            packet,
            images,
            identity,
            observation,
            _references,
            read_preview,
        ) = (
            _policy_documents()
        )
        if identity_mutation is not None:
            assert callable(identity_mutation)
            identity_mutation(identity)
        if observation_mutation is not None:
            assert callable(observation_mutation)
            observation_mutation(observation)
        with mock.patch(
            "mechanical_librarian_workbench.cover_policy.classify_cover_identity",
            return_value=SimpleNamespace(document=canonical_json(identity)),
        ):
            result = adjudicate_cover_policy(
                report_document,
                packet.document,
                images,
                canonical_json(observation) if observation_available else None,
                policy,
                read_preview=read_preview,
            )
        return json.loads(result.document)

    def test_closed_policy_is_content_bound_and_plain_policy_uses_packet_order(self) -> None:
        report = self._adjudicate()
        self.assertEqual(report["status"], "recommended")
        self.assertEqual(
            report["recommended_candidate_reference"],
            report["acceptable_candidate_references"][0],
        )
        self.assertEqual(report["reasons"], ["policy_satisfied"])
        self.assertEqual(report["schema_version"], "cover-policy-report-v1")
        self.assertEqual(
            report["observation_taxonomy_reference"], TAXONOMY_REFERENCE
        )
        self.assertNotEqual(
            CoverPolicy().reference,
            CoverPolicy(avoid_promotional_text=True).reference,
        )
        with self.assertRaises(ValueError):
            CoverPolicy(avoid_promotional_text=1)  # type: ignore[arg-type]

    def test_legacy_v1_observation_remains_admitted_but_v2_requires_attempt_evidence(self) -> None:
        legacy = self._adjudicate(
            observation_mutation=lambda value: (
                value.pop("attempt_evidence"),
                value.update(
                    instruction_sha256=LEGACY_INSTRUCTION_SHA256,
                    schema_version="cover-observation-report-v1",
                ),
            )
        )
        self.assertEqual(legacy["status"], "recommended")
        with self.assertRaises(ValueError):
            self._adjudicate(
                observation_mutation=lambda value: (
                    value.pop("attempt_evidence"),
                    value.update(schema_version="cover-observation-report-v1"),
                )
            )
        with self.assertRaises(ValueError):
            self._adjudicate(
                observation_mutation=lambda value: value.pop("attempt_evidence")
            )
        with self.assertRaises(ValueError):
            self._adjudicate(
                observation_mutation=lambda value: value.update(
                    attempt_evidence=[
                        {
                            "diagnostic_bytes": 17,
                            "diagnostic_sha256": int("1" * 64),
                            "dispatched": True,
                            "failure_code": "local_cli_abort",
                        },
                        {"dispatched": True},
                    ]
                )
            )

    def test_each_closed_policy_value_filters_prefers_or_abstains(self) -> None:
        cases = (
            (
                "promotional present",
                CoverPolicy(avoid_promotional_text=True),
                lambda value: value["observations"][0].update(
                    promotional_text="present"
                ),
                "recommended",
                "promotional_text_excluded",
                2,
            ),
            (
                "promotional unknown",
                CoverPolicy(avoid_promotional_text=True),
                lambda value: value["observations"][0].update(
                    promotional_text="unknown"
                ),
                "abstained",
                "observations_ambiguous",
                0,
            ),
            (
                "tie-in present",
                CoverPolicy(avoid_tie_in_branding=True),
                lambda value: value["observations"][0].update(
                    tie_in_branding="present"
                ),
                "recommended",
                "tie_in_branding_excluded",
                2,
            ),
            (
                "tie-in unknown",
                CoverPolicy(avoid_tie_in_branding=True),
                lambda value: value["observations"][0].update(
                    tie_in_branding="unknown"
                ),
                "abstained",
                "observations_ambiguous",
                0,
            ),
            (
                "minimal preferred",
                CoverPolicy(prefer_minimal_composition=True),
                lambda value: value["observations"][2].update(
                    composition="minimal"
                ),
                "recommended",
                "preferred_minimal",
                3,
            ),
            (
                "composition unknown",
                CoverPolicy(prefer_minimal_composition=True),
                lambda value: value["observations"][0].update(
                    composition="unknown"
                ),
                "abstained",
                "observations_ambiguous",
                0,
            ),
        )
        for name, policy, mutation, status, reason, acceptable in cases:
            with self.subTest(name=name):
                report = self._adjudicate(
                    policy, observation_mutation=mutation
                )
                self.assertEqual(report["status"], status)
                self.assertIn(reason, report["reasons"])
                self.assertEqual(
                    len(report["acceptable_candidate_references"]), acceptable
                )
                if name == "minimal preferred":
                    self.assertEqual(
                        report["recommended_candidate_reference"],
                        report["acceptable_candidate_references"][2],
                    )

    def test_identity_and_empty_policy_sets_fail_closed(self) -> None:
        conflict = self._adjudicate(
            identity_mutation=lambda value: value["candidates"][0].update(
                outcome="conflict"
            )
        )
        self.assertEqual(len(conflict["acceptable_candidate_references"]), 2)
        self.assertIn("conflict_excluded", conflict["reasons"])

        unknown_excluded = self._adjudicate(
            identity_mutation=lambda value: value["candidates"][0].update(
                outcome="unknown"
            )
        )
        self.assertEqual(unknown_excluded["status"], "recommended")
        self.assertEqual(
            len(unknown_excluded["acceptable_candidate_references"]), 2
        )
        self.assertIn("identity_unknown_excluded", unknown_excluded["reasons"])

        none_known = self._adjudicate(
            identity_mutation=lambda value: [
                candidate.update(outcome="unknown")
                for candidate in value["candidates"]
            ]
        )
        self.assertEqual(none_known["status"], "abstained")
        self.assertEqual(
            none_known["reasons"],
            ["identity_unknown_excluded", "no_eligible_candidates"],
        )

        mixed_exclusions = self._adjudicate(
            identity_mutation=lambda value: (
                value["candidates"][0].update(outcome="unknown"),
                value["candidates"][1].update(outcome="conflict"),
            )
        )
        self.assertEqual(mixed_exclusions["status"], "recommended")
        self.assertEqual(
            len(mixed_exclusions["acceptable_candidate_references"]), 1
        )
        self.assertIn("conflict_excluded", mixed_exclusions["reasons"])
        self.assertIn("identity_unknown_excluded", mixed_exclusions["reasons"])

        none_eligible = self._adjudicate(
            identity_mutation=lambda value: [
                candidate.update(outcome="conflict")
                for candidate in value["candidates"]
            ]
        )
        self.assertEqual(none_eligible["status"], "abstained")
        self.assertIn("no_eligible_candidates", none_eligible["reasons"])

        none_acceptable = self._adjudicate(
            CoverPolicy(avoid_promotional_text=True),
            observation_mutation=lambda value: [
                observation.update(promotional_text="present")
                for observation in value["observations"]
            ],
        )
        self.assertEqual(none_acceptable["status"], "abstained")
        self.assertIn("no_policy_compatible_candidate", none_acceptable["reasons"])

    def test_combined_avoid_policy_resolves_exclusion_before_ambiguity(self) -> None:
        policy = CoverPolicy(
            avoid_promotional_text=True,
            avoid_tie_in_branding=True,
        )
        for promotional in ("absent", "present", "unknown"):
            for tie_in in ("absent", "present", "unknown"):
                with self.subTest(promotional=promotional, tie_in=tie_in):
                    report = self._adjudicate(
                        policy,
                        observation_mutation=lambda value, promotional=promotional, tie_in=tie_in: value[
                            "observations"
                        ][0].update(
                            promotional_text=promotional,
                            tie_in_branding=tie_in,
                        ),
                    )
                    if "present" in {promotional, tie_in}:
                        self.assertEqual(report["status"], "recommended")
                        self.assertEqual(
                            len(report["acceptable_candidate_references"]), 2
                        )
                    elif "unknown" in {promotional, tie_in}:
                        self.assertEqual(report["status"], "abstained")
                        self.assertEqual(
                            report["reasons"], ["observations_ambiguous"]
                        )
                    else:
                        self.assertEqual(report["status"], "recommended")
                        self.assertEqual(
                            len(report["acceptable_candidate_references"]), 3
                        )

    def test_unavailable_real_observations_have_one_typed_abstention(self) -> None:
        report = self._adjudicate(observation_available=False)
        self.assertEqual(report["status"], "abstained")
        self.assertEqual(report["reasons"], ["observations_unavailable"])
        self.assertEqual(report["acceptable_candidate_references"], [])
        self.assertIsNone(report["observation_report_sha256"])
        self.assertIsNone(report["recommended_candidate_reference"])

    def test_identity_is_recomputed_from_exact_report_evidence(self) -> None:
        (
            report_document,
            packet,
            images,
            _identity,
            observation,
            _references,
            read_preview,
        ) = (
            _policy_documents()
        )
        report = json.loads(
            adjudicate_cover_policy(
                report_document,
                packet.document,
                images,
                canonical_json(observation),
                CoverPolicy(),
                read_preview=read_preview,
            ).document
        )
        self.assertEqual(report["status"], "abstained")
        self.assertEqual(
            report["reasons"],
            ["identity_unknown_excluded", "no_eligible_candidates"],
        )

    def test_unknown_invented_duplicate_or_mismatched_inputs_fail_closed(self) -> None:
        mutations = (
            lambda observation: observation.update(
                taxonomy_reference="cover_taxonomy_" + "0" * 64
            ),
            lambda observation: observation["observations"][0].update(
                candidate_reference="cover_ref_" + "0" * 64
            ),
            lambda observation: observation["observations"].__setitem__(
                1, observation["observations"][0]
            ),
            lambda observation: observation["observations"][0].update(
                preview_sha256="0" * 64
            ),
            lambda observation: observation["observations"][0].update(
                medium="/private/hostile"
            ),
            lambda observation: observation.update(
                attempt_evidence=[
                    {
                        "diagnostic_bytes": 1,
                        "diagnostic_sha256": "a" * 64,
                        "dispatched": True,
                        "failure_code": "runtime",
                    },
                    {"dispatched": True},
                ]
            ),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                (
                    report_document,
                    packet,
                    images,
                    identity,
                    observation,
                    _references,
                    read_preview,
                ) = (
                    _policy_documents()
                )
                mutation(observation)
                with mock.patch(
                    "mechanical_librarian_workbench.cover_policy.classify_cover_identity",
                    return_value=SimpleNamespace(document=canonical_json(identity)),
                ):
                    with self.assertRaises(ValueError):
                        adjudicate_cover_policy(
                            report_document,
                            packet.document,
                            images,
                            canonical_json(observation),
                            CoverPolicy(),
                            read_preview=read_preview,
                        )


if __name__ == "__main__":
    unittest.main()
