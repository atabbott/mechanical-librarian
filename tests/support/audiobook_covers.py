"""Controlled synthetic audiobook cover considerations through actual consumers."""

import contextlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Never

from mechanical_librarian.adapters import cover_artifacts as decoder
from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.audiobook_reports import (
    asserted_asin_report,
    publish_audiobook_preview,
)
from tests.support.cover_images import synthetic_image


@contextlib.contextmanager
def audiobook_cover_environment(
    out: Path,
    sizes: list[tuple[int, int]],
    *,
    unknown_incumbent: bool = False,
    option: str = "replace_if_strictly_better",
) -> Iterator[Any]:
    out.mkdir(mode=0o700)
    originals = [synthetic_image("PNG", size) for size in sizes]
    decoded = [decoder.decode_cover(payload) for payload in originals]
    report = asserted_asin_report(provider_duration="58200.000")
    provider = report.providers[0]
    candidate = provider.candidates[0]
    provenance = CoverProvenance(
        provider.provider,
        CoverDesignation.RELATED_EDITION,
        candidate.work_identity,
        candidate.identity,
    )
    sources = [
        CoverSource(
            cover_source_id(CoverSourceKind.AUDIBLE_CATALOG, f"synthetic-{i}"),
            CoverSourceKind.AUDIBLE_CATALOG,
            CoverSourceState.RETRIEVED,
            item.artifact,
            provenance=provenance,
        )
        for i, item in enumerate(decoded)
        if not (unknown_incumbent and i == 0)
    ]
    sources.append(
        CoverSource(
            cover_source_id(CoverSourceKind.ABS, "synthetic-incumbent"),
            CoverSourceKind.ABS,
            CoverSourceState.RETRIEVED,
            decoded[0].artifact,
        )
    )
    fields = tuple(
        TargetField(
            field.destination, TargetFieldState.VALUE, decoded[0].artifact.artifact_id
        )
        if field.destination is TargetDestination.COVER
        else field
        for field in report.target.fields
    )
    report = MetadataReport.create(
        target=replace(
            report.target,
            connection="primary",
            library="audio",
            item="item-1",
            fields=fields,
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        audio=report.audio,
        cover_sources=tuple(sorted(sources, key=lambda source: source.source_id)),
    )
    artifacts = CoverArtifactStore(out / "artifacts")
    for original, item in zip(originals, decoded, strict=True):
        artifacts.stage(item.artifact, original, item.preview)
    repository = open_repository(out / "state.sqlite")
    try:
        case_id, resolved = publish_audiobook_preview(
            repository,
            report,
            ("cover_order",),
            selected_options={"cover_order": option},
        )
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(row for row in case.proposals if row["destination"] == "cover")

        def no_observer(*args: object, **kwargs: object) -> Never:
            raise AssertionError("image observer prohibited")

        service = CoverConsiderationService(
            repository,
            read_preview=lambda identity, variant: artifacts.preview(
                next(
                    source.artifact
                    for source in report.cover_sources
                    if source.artifact is not None
                    and source.artifact.artifact_id == identity
                )
            ),
            read_preview_digest=artifacts.read_preview_digest,
            clock=lambda: 10,
            reviewer_dispatch=no_observer,
        )
        request = service.prepare_observation(case_id, report.to_json())
        assert request.packet_payload is None and request.image_payloads == ()
        service.ensure(case_id, report.to_json(), proposal)
        documents = repository.get_review_case_decisions(case_id)
        consideration_document = repository.current_cover_consideration_document(
            case_id
        )
        assert consideration_document is not None
        consideration = json.loads(consideration_document)
        repository.close()
        repository = open_repository(out / "state.sqlite")
        assert repository.get_review_case_decisions(case_id) == documents
        decisions = [CoverAutomaticDecision.from_json(raw) for raw in documents]
        snapshots = [
            PersistedFieldEffectAuthority(repository).resolve(decision)
            for decision in decisions
        ]
        yield SimpleNamespace(
            repo=repository,
            report=report,
            case=case,
            resolved=resolved,
            consideration=consideration,
            decisions=decisions,
            snapshots=snapshots,
            decoded=decoded,
            originals=originals,
            artifacts=artifacts,
        )
    finally:
        repository.close()
