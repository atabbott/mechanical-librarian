"""Synthetic Inventaire outcomes injected before real preparation publication."""

import io
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from PIL import Image

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.providers.inventaire_metadata import (
    InventaireMetadataProvider,
    InventairePacer,
)
from mechanical_librarian.adapters.providers.remote_cover import (
    CoverDeadline,
    RemoteCover,
)
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    ProviderCoverDescriptor,
    qualify_provider_observation,
)
from mechanical_librarian.bootstrap.composition import _CoverEvidenceObserver
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
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
)


class InventairePreparationFixture:
    """Change observation input while retaining the production preparation queue."""

    def __init__(
        self,
        state: CoverSourceState = CoverSourceState.UNAVAILABLE,
        artifacts: Path | None = None,
    ) -> None:
        self.fail = True
        self.state = state
        self.artifacts = artifacts
        self._cover_fetch_lock = threading.Lock()

    def __call__(self, seed: MetadataReport) -> MetadataReport:
        source_id = cover_source_id(CoverSourceKind.INVENTAIRE, seed.target.item)
        if self.fail and seed.target.item == "fiction-0000":
            source_id = source_id[:-1]
        source = CoverSource(
            source_id,
            CoverSourceKind.INVENTAIRE,
            CoverSourceState.UNAVAILABLE,
            provenance=CoverProvenance(
                "inventaire", CoverDesignation.WORK, "inventaire-work-wd-Q1"
            ),
        )
        if self.state is CoverSourceState.RETRIEVED:
            assert self.artifacts is not None
            image = io.BytesIO()
            Image.new("RGB", (10, 20), "red").save(image, "JPEG")
            descriptor = ProviderCoverDescriptor(
                "inventaire",
                CoverSourceKind.INVENTAIRE,
                source_id,
                "https://inventaire.io/img/entities/" + "a" * 40,
                source.provenance,
            )
            with (
                self._cover_fetch_lock,
                patch(
                    "mechanical_librarian.bootstrap.composition.fetch_remote_cover",
                    return_value=RemoteCover(
                        descriptor.url or "", "image/jpeg", image.getvalue()
                    ),
                ),
            ):
                source = _CoverEvidenceObserver(
                    Mock(), CoverArtifactStore(self.artifacts)
                )._remote(
                    descriptor,
                    CoverDeadline(time.monotonic() + 10, time.monotonic() + 10),
                )
            assert source.state is CoverSourceState.RETRIEVED
        else:
            source = replace(
                source,
                state=self.state,
                timeout_scope=(
                    "not_started_due_to_aggregate_deadline"
                    if self.state is CoverSourceState.TIMEOUT
                    else None
                ),
            )
        title = seed.target.current_title or seed.target.item
        lookup = MetadataLookup(
            ProviderQueryKind.WORK, seed.local_claims, title, ("Ada Example",)
        )

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/search":
                return httpx.Response(
                    200,
                    json={"results": [{"id": "Q1", "uri": "wd:Q1", "type": "works"}]},
                )
            uris = request.url.params.get_list("uris")
            if uris == ["wd:Q1"]:
                entity = {
                    "uri": "wd:Q1",
                    "claims": {"wdt:P1476": [title], "wdt:P50": ["wd:Q42"]},
                }
            else:
                assert uris == ["wd:Q42"]
                entity = {"uri": "wd:Q42", "labels": {"en": "Ada Example"}}
            return httpx.Response(
                200, json={"entities": {uris[0]: entity}, "redirects": {}}
            )

        provider = InventaireMetadataProvider(
            "inventaire",
            httpx.Client(transport=httpx.MockTransport(respond)),
            65_536,
            pacer=InventairePacer(0.001, sleep=lambda _: None),
        )
        observation = qualify_provider_observation(
            (provider.observe(lookup)).observation, lookup
        )
        assert observation.status is ProviderStatus.CANDIDATES
        target = replace(
            seed.target,
            fields=tuple(
                TargetField(
                    TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada Example",)
                )
                if field.destination is TargetDestination.AUTHORS
                else field
                for field in seed.target.fields
            ),
        )
        return MetadataReport.create(
            target=target,
            file=seed.file,
            local_claims=seed.local_claims,
            local_reasons=seed.local_reasons,
            providers=(observation,),
            cover_sources=(source,),
        )
