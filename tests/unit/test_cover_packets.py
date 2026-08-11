from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

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
    ContainedReviewImage,
    ContainedReviewRequest,
)
from mechanical_librarian_workbench.cover_packets import (  # noqa: E402
    MAX_COVER_PREVIEWS,
    prepare_cover_packet,
)
from mechanical_librarian_workbench import (  # noqa: E402
    CoverPacketRequest,
    IsolatedNetworkProfile,
    WorkbenchContext,
    WorkbenchOperations,
    execute,
)


def _preview(index: int, *, size: int = 32) -> bytes:
    prefix = b"\x89PNG\r\n\x1a\n" + index.to_bytes(2, "big")
    return prefix + bytes([index % 251]) * (size - len(prefix))


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
            run_contained_reviewer=lambda *args: observed.append(args) or '{"status":"completed"}',
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
        self.assertEqual(observed[0][3], packet.document.encode())
        image_arguments = observed[0][5]
        self.assertIsInstance(image_arguments, tuple)
        assert isinstance(image_arguments, tuple)
        self.assertEqual(
            [(value[0], value[1]) for value in image_arguments],
            [(value.reference, value.digest) for value in packet.images],
        )
        self.assertEqual(len(image_arguments), 5)

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
        payload = _preview(1)
        image = ContainedReviewImage(
            "cover_ref_" + "a" * 64, hashlib.sha256(payload).hexdigest(), payload
        )
        schema = json.dumps(
            {"additionalProperties": False, "properties": {}, "type": "object"},
            separators=(",", ":"), sort_keys=True,
        )
        request = ContainedReviewRequest("Inspect.", '{"packet":true}', schema, (image,))
        changed = ContainedReviewRequest(
            "Inspect.", '{"packet":true}', schema,
            (ContainedReviewImage("cover_ref_" + "b" * 64, image.digest, payload),),
        )
        self.assertNotEqual(request.digest, changed.digest)
        with self.assertRaisesRegex(Exception, "response_format"):
            ContainedReviewImage(image.reference, "0" * 64, payload)

    def test_image_count_and_aggregate_byte_limits_are_absolute(self) -> None:
        schema = json.dumps(
            {"additionalProperties": False, "properties": {}, "type": "object"},
            separators=(",", ":"), sort_keys=True,
        )

        def image(index: int, size: int = 32) -> ContainedReviewImage:
            payload = _preview(index, size=size)
            return ContainedReviewImage(
                "cover_ref_" + f"{index:064x}",
                hashlib.sha256(payload).hexdigest(),
                payload,
            )

        images = tuple(image(index) for index in range(1, 13))
        self.assertEqual(
            len(ContainedReviewRequest("Inspect.", '{"packet":true}', schema, images).images),
            12,
        )
        with self.assertRaisesRegex(Exception, "response_format"):
            ContainedReviewRequest(
                "Inspect.", '{"packet":true}', schema, images + (image(13),)
            )
        with self.assertRaisesRegex(Exception, "response_format"):
            ContainedReviewRequest(
                "Inspect.", '{"packet":true}', schema,
                tuple(image(index, size=1_048_576) for index in range(1, 10)),
            )


if __name__ == "__main__":
    unittest.main()
