"""Synthetic canonical previews for taste profile builds (#1372)."""

from __future__ import annotations

import hashlib
import io
import json
import random
from dataclasses import replace
from pathlib import Path

from PIL import Image

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_packet_semantics import (
    NormalizedCoverPreview,
    normalize_cover_preview,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.review_web import RecordHumanDecision
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.cover_producers import _qualified_cover_report


def stored_preview(index: int, size: tuple[int, int] = (32, 48)) -> bytes:
    """A small flat-colour preview as the artifact store holds one."""
    output = io.BytesIO()
    colour = (index % 251, (index * 3) % 251, (index * 7) % 251, 255)
    Image.new("RGBA", size, colour).save(output, "PNG")
    return output.getvalue()


def noise_preview(seed: int) -> bytes:
    """A 320 × 480 noise preview: the largest a canonical preview can be."""
    rng = random.Random(seed)
    output = io.BytesIO()
    Image.frombytes("RGBA", (320, 480), rng.randbytes(320 * 480 * 4)).save(
        output, "PNG"
    )
    return output.getvalue()


def canonical(payload: bytes) -> NormalizedCoverPreview:
    """The canonical PNG a consideration records for a stored preview."""
    return normalize_cover_preview(
        payload, source_sha256=hashlib.sha256(payload).hexdigest()
    )


def record_owner_cover_decisions(
    root: Path,
    repository: SQLiteMetadataReportRepository,
    count: int,
    *,
    start: int = 0,
) -> None:
    """Record `count` owner cover choices through the real decision command.

    Each item's consideration offers its current cover and one larger
    candidate of another art; the owner chooses the candidate.
    """
    base, _ = _qualified_cover_report(root, candidate_size=(1200, 1800))
    artifacts = CoverArtifactStore(root / "artifacts")
    policy = CoverPolicy()
    repository.bind_current_cover_policy(policy.reference)
    digests = {
        source.artifact.artifact_id: source.artifact.preview_digest
        for source in base.cover_sources
        if source.artifact is not None
    }
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
            digests[artifact_id]
        ),
        read_preview_digest=artifacts.read_preview_digest,
        require_artifact=artifacts.require_artifact,
        clock=lambda: 100,
        policy=policy,
    )
    repository.bind_current_cover_replay(service.replay_reference)
    repository.bind_owner_cover_validation(service.require_owner_cover)
    repository.bind_cover_consideration_currentness(lambda *_: False)
    for index in range(start, start + count):
        report = MetadataReport.create(
            target=replace(base.target, item=f"item-{index}"),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=base.cover_sources,
        )
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(row for row in case.proposals if row["destination"] == "cover")
        consideration = service.ensure(case_id, report.to_json(), proposal)
        chosen = next(
            candidate
            for candidate in consideration["candidates"]
            if not candidate["incumbent"] and candidate["selectable"]
        )
        decided_at = 200 + index
        RecordHumanDecision(repository, lambda: decided_at).run(  # noqa: B023 - run now
            case_id,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination="cover",
            actor="owner",
            outcome="accept",
            selected_value={"kind": "cover", "value": chosen["artifact_id"]},
            idempotency_key=f"synthetic-taste-owner-{index:04d}",
            consideration_id=str(consideration["consideration_id"]),
            cover_confirmation={
                "expected_selection_id": None,
                "confirm_unverified_match": True,
            },
        )
