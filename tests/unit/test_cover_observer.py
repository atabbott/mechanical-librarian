import io
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from mechanical_librarian.adapters.cover_artifacts import CoverDecodeTimeout
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.providers.remote_cover import (
    CoverDeadline,
    RemoteCover,
)
from mechanical_librarian.application.configuration import (
    ProviderConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    CoverReadError,
    CoverSourceChangedError,
    CoverTimeoutError,
    ProviderCoverDescriptor,
    TargetCover,
    TargetSnapshot,
)
from mechanical_librarian.bootstrap.composition import _CoverEvidenceObserver
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSourceKind,
    CoverSourceState,
    CoverTruncationReason,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ProviderCandidate,
    ProviderObservation,
    ProviderStatus,
)


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (10, 20), "red").save(output, "JPEG")
    return output.getvalue()


class _Target:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def read(self, item: str) -> TargetSnapshot:
        raise AssertionError("not used")

    def read_cover(
        self, cover: TargetCover, *, deadline: float | None = None
    ) -> bytes | None:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]


def test_gallery_admission_caps_provider_and_total_with_visible_truncation() -> None:
    configurations = tuple(
        ProviderConnectionConfiguration(
            name,
            True,
            True,
            f"/secrets/{name}",
        )
        for name in ("alpha", "beta", "gamma")
    )
    observations = tuple(
        ProviderObservation(
            name,
            ProviderStatus.CANDIDATES,
            (),
            (
                ProviderCandidate(
                    f"{name}-edition",
                    f"{name}-work",
                    (),
                ),
            ),
        )
        for name in ("alpha", "beta", "gamma")
    )
    descriptors = tuple(
        ProviderCoverDescriptor(
            provider,
            CoverSourceKind.HARDCOVER,
            cover_source_id(
                CoverSourceKind.HARDCOVER,
                f"{provider}:edition:{index}",
            ),
            None,
            CoverProvenance(
                provider,
                CoverDesignation.RELATED_EDITION,
                f"{provider}-work",
                f"{provider}-edition-{index}",
            ),
        )
        for provider in ("alpha", "beta", "gamma")
        for index in range(5)
    )
    with tempfile.TemporaryDirectory() as directory:
        values = _CoverEvidenceObserver(
            _Target(None),
            CoverArtifactStore(Path(directory)),
            configurations,
        ).observe(
            TargetSnapshot("ebook", "Title", (), None),
            observations,
            (),
            descriptors,
        )
    provider_values = [
        value for value in values if value.kind is CoverSourceKind.HARDCOVER
    ]
    admitted = [
        value
        for value in provider_values
        if value.state is not CoverSourceState.TRUNCATED
    ]
    assert 8 == len(admitted)
    assert all(
        sum(
            value.provenance is not None and value.provenance.provider == provider
            for value in admitted
        )
        <= 4
        for provider in ("alpha", "beta", "gamma")
    )
    reasons = {
        value.provenance.truncation_reason
        for value in provider_values
        if value.state is CoverSourceState.TRUNCATED and value.provenance is not None
    }
    assert {
        CoverTruncationReason.PROVIDER_LIMIT,
        CoverTruncationReason.GALLERY_LIMIT,
    } == reasons


def test_digest_deduplication_retains_every_provider_provenance() -> None:
    configuration = ProviderConnectionConfiguration(
        "catalogue",
        True,
        True,
        "/secrets/catalogue",
    )
    observation = ProviderObservation(
        "catalogue",
        ProviderStatus.CANDIDATES,
        (),
        (ProviderCandidate("edition-1", "work-1", ()),),
    )
    descriptors = tuple(
        ProviderCoverDescriptor(
            "catalogue",
            CoverSourceKind.HARDCOVER,
            cover_source_id(CoverSourceKind.HARDCOVER, identity),
            f"https://example.com/{identity}.jpg",
            CoverProvenance(
                "catalogue",
                designation,
                "work-1",
                identity if designation is CoverDesignation.RELATED_EDITION else None,
            ),
        )
        for identity, designation in (
            ("edition-2", CoverDesignation.RELATED_EDITION),
            ("work-1", CoverDesignation.WORK),
        )
    )
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "mechanical_librarian.bootstrap.composition.fetch_remote_cover",
            return_value=RemoteCover(
                "https://example.com/cover.jpg",
                "image/jpeg",
                _jpeg(),
            ),
        ),
    ):
        values = _CoverEvidenceObserver(
            _Target(None),
            CoverArtifactStore(Path(directory)),
            (configuration,),
        ).observe(
            TargetSnapshot("ebook", "Title", (), None),
            (observation,),
            (),
            descriptors,
        )
    retrieved = [value for value in values if value.state is CoverSourceState.RETRIEVED]
    assert 2 == len(retrieved)
    assert 1 == len(
        {
            value.artifact.original_digest
            for value in retrieved
            if value.artifact is not None
        }
    )
    assert {CoverDesignation.RELATED_EDITION, CoverDesignation.WORK} == {
        value.provenance.designation
        for value in retrieved
        if value.provenance is not None
    }


def test_terminal_descriptor_state_is_published_without_remote_fetch() -> None:
    configuration = ProviderConnectionConfiguration(
        "catalogue",
        True,
        True,
        "/secrets/catalogue",
    )
    observation = ProviderObservation(
        "catalogue",
        ProviderStatus.CANDIDATES,
        (),
        (ProviderCandidate("edition-1", "work-1", ()),),
    )
    descriptor = ProviderCoverDescriptor(
        "catalogue",
        CoverSourceKind.HARDCOVER,
        cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:edition-1"),
        None,
        CoverProvenance(
            "catalogue",
            CoverDesignation.RELATED_EDITION,
            "work-1",
            "edition-1",
        ),
        terminal_state=CoverSourceState.REJECTED,
    )
    with (
        tempfile.TemporaryDirectory() as directory,
        patch("mechanical_librarian.bootstrap.composition.fetch_remote_cover") as fetch,
    ):
        values = _CoverEvidenceObserver(
            _Target(None),
            CoverArtifactStore(Path(directory)),
            (configuration,),
        ).observe(
            TargetSnapshot("ebook", "Title", (), None),
            (observation,),
            (),
            (descriptor,),
        )
    provider_value = next(
        value for value in values if value.kind is CoverSourceKind.HARDCOVER
    )
    assert CoverSourceState.REJECTED == provider_value.state
    fetch.assert_not_called()


def test_abs_result_is_recorded_without_a_request() -> None:
    with tempfile.TemporaryDirectory() as directory:
        target = _Target(b"unexpected")
        value = _CoverEvidenceObserver(
            target, CoverArtifactStore(Path(directory))
        ).observe(TargetSnapshot("ebook", "Title", (), None), (), ())
    assert (CoverSourceKind.ABS, CoverSourceState.ABSENT) == (
        value[0].kind,
        value[0].state,
    )
    assert 0 == target.calls


def test_retrieves_and_stages_one_declared_abs_cover() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = CoverArtifactStore(Path(directory))
        target = _Target(_jpeg())
        value = _CoverEvidenceObserver(target, store).observe(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
            (),
            (),
        )
        assert CoverSourceState.RETRIEVED == value[0].state
        assert 1 == target.calls
        assert b"\x89PNG\r\n\x1a\n" == store.preview(value[0].artifact)[:8]


def test_timeout_is_an_explicit_attempted_outcome() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = _CoverEvidenceObserver(
            _Target(CoverTimeoutError("elapsed")), CoverArtifactStore(Path(directory))
        ).observe(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
            (),
            (),
        )
    assert (CoverSourceState.TIMEOUT, "attempted") == (
        value[0].state,
        value[0].timeout_scope,
    )


def test_changed_abs_source_is_recorded_without_publication() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = _CoverEvidenceObserver(
            _Target(CoverSourceChangedError("changed")),
            CoverArtifactStore(Path(directory)),
        ).observe(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
            (),
            (),
        )
    assert CoverSourceState.SOURCE_CHANGED == value[0].state


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        ("oversized", CoverSourceState.OVERSIZED),
        ("unsupported_format", CoverSourceState.UNSUPPORTED_FORMAT),
    ),
    ids=("oversized", "unsupported_format"),
)
def test_abs_terminal_read_classifications_are_preserved(
    state: str, expected: CoverSourceState
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = _CoverEvidenceObserver(
            _Target(CoverReadError(state)),
            CoverArtifactStore(Path(directory)),
        ).observe(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
            (),
            (),
        )
    assert expected == value[0].state


def test_abs_decoder_uses_aggregate_deadline_and_maps_expiry_to_timeout() -> None:
    aggregate = time.monotonic() + 10.0
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "mechanical_librarian.bootstrap.composition.decode_cover",
            side_effect=CoverDecodeTimeout("elapsed"),
        ) as decode,
    ):
        value = _CoverEvidenceObserver(
            _Target(_jpeg()),
            CoverArtifactStore(Path(directory)),
        ).observe(
            TargetSnapshot(
                "ebook",
                "Title",
                (),
                TargetCover("descriptor", "/cover.jpg"),
            ),
            (),
            (),
            aggregate_deadline=aggregate,
        )
    decode.assert_called_once_with(_jpeg(), deadline=aggregate)
    assert (CoverSourceState.TIMEOUT, "attempted") == (
        value[0].state,
        value[0].timeout_scope,
    )


def test_remote_decoder_uses_aggregate_deadline_and_maps_expiry_to_timeout() -> None:
    aggregate = time.monotonic() + 10.0
    source_id = cover_source_id(CoverSourceKind.HARDCOVER, "candidate")
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "mechanical_librarian.bootstrap.composition.fetch_remote_cover",
            return_value=RemoteCover(
                "https://example.com/cover.jpg",
                "image/jpeg",
                _jpeg(),
            ),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.decode_cover",
            side_effect=CoverDecodeTimeout("elapsed"),
        ) as decode,
    ):
        value = _CoverEvidenceObserver(
            _Target(None),
            CoverArtifactStore(Path(directory)),
        )._remote(
            ProviderCoverDescriptor(
                "hardcover",
                CoverSourceKind.HARDCOVER,
                source_id,
                "https://example.com/cover.jpg",
            ),
            CoverDeadline(aggregate, aggregate),
        )
    decode.assert_called_once_with(_jpeg(), deadline=aggregate)
    assert (CoverSourceState.TIMEOUT, "attempted") == (
        value.state,
        value.timeout_scope,
    )
