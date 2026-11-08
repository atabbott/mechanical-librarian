import io
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from mechanical_librarian.adapters.cover_artifacts import CoverDecodeTimeout
from mechanical_librarian.adapters.cover_evidence_observer import CoverEvidenceObserver
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
    CoverRedirects,
    CoverSourceChangedError,
    CoverTimeoutError,
    ProviderCoverDescriptor,
    TargetCover,
    TargetSnapshot,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverEvidenceError,
    CoverFinalHost,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    CoverTruncationReason,
    cover_source_id,
    publication_ready,
)
from mechanical_librarian.domain.cover_rules import CoverMeasures
from mechanical_librarian.domain.metadata_report import (
    ProviderCandidate,
    ProviderObservation,
    ProviderStatus,
)
from tests.unit.test_remote_cover import (
    _AGENT,
    _COVER_URL,
    _HOP1,
    _HOP2,
    _image,
    _redirect,
    _Route,
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
        values = CoverEvidenceObserver(
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
    # Each connection's first four fill the twelve; no fifth finds room.
    assert 12 == len(admitted)
    assert all(
        sum(
            value.provenance is not None and value.provenance.provider == provider
            for value in admitted
        )
        == 4
        for provider in ("alpha", "beta", "gamma")
    )
    reasons = {
        value.provenance.truncation_reason
        for value in provider_values
        if value.state is CoverSourceState.TRUNCATED and value.provenance is not None
    }
    assert {CoverTruncationReason.GALLERY_LIMIT} == reasons


@pytest.mark.parametrize(
    "kind", (CoverSourceKind.HARDCOVER, CoverSourceKind.INVENTAIRE)
)
def test_digest_deduplication_retains_every_provider_provenance(
    kind: CoverSourceKind,
) -> None:
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
            kind,
            cover_source_id(kind, identity),
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
            "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
            return_value=RemoteCover(
                "https://example.com/cover.jpg",
                "image/jpeg",
                _jpeg(),
            ),
        ),
    ):
        values = CoverEvidenceObserver(
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


@pytest.mark.parametrize(
    "kind", (CoverSourceKind.HARDCOVER, CoverSourceKind.INVENTAIRE)
)
def test_terminal_descriptor_state_is_published_without_remote_fetch(
    kind: CoverSourceKind,
) -> None:
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
        kind,
        cover_source_id(kind, "catalogue:edition-1"),
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
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover"
        ) as fetch,
    ):
        values = CoverEvidenceObserver(
            _Target(None),
            CoverArtifactStore(Path(directory)),
            (configuration,),
        ).observe(
            TargetSnapshot("ebook", "Title", (), None),
            (observation,),
            (),
            (descriptor,),
        )
    provider_value = next(value for value in values if value.kind is kind)
    assert CoverSourceState.REJECTED == provider_value.state
    fetch.assert_not_called()


_CATALOGUE = ProviderConnectionConfiguration(
    "catalogue",
    True,
    True,
    "/secrets/catalogue",
)


def _candidates_observation(
    provider: str, edition: str, work: str
) -> ProviderObservation:
    return ProviderObservation(
        provider,
        ProviderStatus.CANDIDATES,
        (),
        (ProviderCandidate(edition, work, ()),),
    )


def _provider_descriptor(
    designation: CoverDesignation,
    work_identity: str,
    edition_identity: str | None,
) -> ProviderCoverDescriptor:
    return ProviderCoverDescriptor(
        "catalogue",
        CoverSourceKind.HARDCOVER,
        cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:cover"),
        "https://example.com/cover.jpg",
        CoverProvenance("catalogue", designation, work_identity, edition_identity),
    )


def _provider_cover_rows(
    descriptor: ProviderCoverDescriptor,
    observations: tuple[ProviderObservation, ...],
) -> tuple[list[CoverSource], Mock]:
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
            return_value=RemoteCover(
                "https://example.com/cover.jpg",
                "image/jpeg",
                _jpeg(),
            ),
        ) as fetch,
    ):
        values = CoverEvidenceObserver(
            _Target(None),
            CoverArtifactStore(Path(directory)),
            (_CATALOGUE,),
        ).observe(
            TargetSnapshot("ebook", "Title", (), None),
            observations,
            (),
            (descriptor,),
        )
    return [value for value in values if value.kind is CoverSourceKind.HARDCOVER], fetch


def test_an_unresolved_work_descriptor_is_admitted_on_an_observed_edition() -> None:
    rows, fetch = _provider_cover_rows(
        _provider_descriptor(
            CoverDesignation.RELATED_EDITION, "unresolved", "edition-1"
        ),
        (_candidates_observation("catalogue", "edition-1", "unresolved"),),
    )

    assert [CoverSourceState.RETRIEVED] == [value.state for value in rows]
    assert 1 == fetch.call_count


def test_a_resolved_work_descriptor_keeps_the_existing_same_provider_work_match() -> (
    None
):
    rows, fetch = _provider_cover_rows(
        _provider_descriptor(
            CoverDesignation.RELATED_EDITION, "work-1", "edition-unobserved"
        ),
        (_candidates_observation("catalogue", "edition-1", "work-1"),),
    )

    assert [CoverSourceState.RETRIEVED] == [value.state for value in rows]
    assert 1 == fetch.call_count


@pytest.mark.parametrize(
    ("descriptor", "observations"),
    (
        pytest.param(
            _provider_descriptor(
                CoverDesignation.RELATED_EDITION, "unresolved", "edition-other"
            ),
            (_candidates_observation("catalogue", "edition-1", "unresolved"),),
            id="different_edition",
        ),
        pytest.param(
            _provider_descriptor(CoverDesignation.WORK, "unresolved", None),
            (_candidates_observation("catalogue", "edition-1", "unresolved"),),
            id="absent_edition",
        ),
        pytest.param(
            _provider_descriptor(
                CoverDesignation.RELATED_EDITION, "unresolved", "edition-1"
            ),
            (
                _candidates_observation("catalogue", "edition-2", "unresolved"),
                _candidates_observation("other", "edition-1", "unresolved"),
            ),
            id="edition_observed_by_another_provider",
        ),
    ),
)
def test_an_unresolved_work_descriptor_is_refused_before_any_cover_fetch(
    descriptor: ProviderCoverDescriptor,
    observations: tuple[ProviderObservation, ...],
) -> None:
    """Two unresolved sentinels are equal and mean nothing, so they admit nothing."""
    rows, fetch = _provider_cover_rows(descriptor, observations)

    assert [] == rows
    fetch.assert_not_called()


def test_abs_result_is_recorded_without_a_request() -> None:
    with tempfile.TemporaryDirectory() as directory:
        target = _Target(b"unexpected")
        value = CoverEvidenceObserver(
            target, CoverArtifactStore(Path(directory))
        ).observe_current(TargetSnapshot("ebook", "Title", (), None), ())
    assert (CoverSourceKind.ABS, CoverSourceState.ABSENT) == (
        value[0].kind,
        value[0].state,
    )
    assert 0 == target.calls


def test_retrieves_and_stages_one_declared_abs_cover() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = CoverArtifactStore(Path(directory))
        target = _Target(_jpeg())
        value = CoverEvidenceObserver(target, store).observe_current(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
            (),
        )
        assert CoverSourceState.RETRIEVED == value[0].state
        assert 1 == target.calls
        assert b"\x89PNG\r\n\x1a\n" == store.preview(value[0].artifact)[:8]


def test_timeout_is_an_explicit_attempted_outcome() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = CoverEvidenceObserver(
            _Target(CoverTimeoutError("elapsed")), CoverArtifactStore(Path(directory))
        ).observe_current(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
            (),
        )
    assert (CoverSourceState.TIMEOUT, "attempted") == (
        value[0].state,
        value[0].timeout_scope,
    )


def test_changed_abs_source_is_recorded_without_publication() -> None:
    with tempfile.TemporaryDirectory() as directory:
        value = CoverEvidenceObserver(
            _Target(CoverSourceChangedError("changed")),
            CoverArtifactStore(Path(directory)),
        ).observe_current(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
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
        value = CoverEvidenceObserver(
            _Target(CoverReadError(state)),
            CoverArtifactStore(Path(directory)),
        ).observe_current(
            TargetSnapshot(
                "ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")
            ),
            (),
        )
    assert expected == value[0].state


def test_abs_decoder_uses_aggregate_deadline_and_maps_expiry_to_timeout() -> None:
    aggregate = time.monotonic() + 10.0
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.decode_cover",
            side_effect=CoverDecodeTimeout("elapsed"),
        ) as decode,
    ):
        value = CoverEvidenceObserver(
            _Target(_jpeg()),
            CoverArtifactStore(Path(directory)),
        ).observe_current(
            TargetSnapshot(
                "ebook",
                "Title",
                (),
                TargetCover("descriptor", "/cover.jpg"),
            ),
            (),
            aggregate_deadline=aggregate,
        )
    decode.assert_called_once_with(_jpeg(), deadline=aggregate)
    assert (CoverSourceState.TIMEOUT, "attempted") == (
        value[0].state,
        value[0].timeout_scope,
    )


@pytest.mark.parametrize(
    "kind", (CoverSourceKind.HARDCOVER, CoverSourceKind.INVENTAIRE)
)
def test_remote_decoder_uses_aggregate_deadline_and_maps_expiry_to_timeout(
    kind: CoverSourceKind,
) -> None:
    aggregate = time.monotonic() + 10.0
    source_id = cover_source_id(kind, "candidate")
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
            return_value=RemoteCover(
                "https://example.com/cover.jpg",
                "image/jpeg",
                _jpeg(),
            ),
        ),
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.decode_cover",
            side_effect=CoverDecodeTimeout("elapsed"),
        ) as decode,
    ):
        value = CoverEvidenceObserver(
            _Target(None),
            CoverArtifactStore(Path(directory)),
        )._remote(
            ProviderCoverDescriptor(
                "hardcover",
                kind,
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


@pytest.mark.parametrize("outcome", ("absent", "unavailable", "deadline"))
def test_inventaire_remote_nonretrieved_outcomes(outcome: str, tmp_path: Path) -> None:
    from mechanical_librarian.adapters.providers.remote_cover import RemoteCoverError

    descriptor = ProviderCoverDescriptor(
        "inventaire",
        CoverSourceKind.INVENTAIRE,
        cover_source_id(CoverSourceKind.INVENTAIRE, "work-1"),
        None
        if outcome == "absent"
        else "https://inventaire.io/img/entities/" + "a" * 40,
        CoverProvenance("inventaire", CoverDesignation.WORK, "work-1"),
        wait=(lambda _: False) if outcome == "deadline" else None,
    )
    with patch(
        "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
        side_effect=RemoteCoverError("unavailable"),
    ) as fetch:
        source = CoverEvidenceObserver(
            _Target(None), CoverArtifactStore(tmp_path)
        )._remote(
            descriptor, CoverDeadline(time.monotonic() + 10, time.monotonic() + 10)
        )
    assert (
        source.state
        is {
            "absent": CoverSourceState.ABSENT,
            "unavailable": CoverSourceState.UNAVAILABLE,
            "deadline": CoverSourceState.TIMEOUT,
        }[outcome]
    )
    if outcome == "deadline":
        assert source.timeout_scope == "not_started_due_to_aggregate_deadline"
    assert fetch.call_count == (1 if outcome == "unavailable" else 0)


@pytest.mark.parametrize("declared", (False, True))
def test_expired_current_acquisition_does_not_dispatch(declared, tmp_path):
    target = _Target(_jpeg())
    snapshot = TargetSnapshot(
        "ebook",
        "Title",
        (),
        TargetCover("descriptor", "/cover.jpg") if declared else None,
    )
    sources = CoverEvidenceObserver(
        target, CoverArtifactStore(tmp_path)
    ).observe_current(snapshot, (), time.monotonic() - 1)
    assert target.calls == 0
    assert sources[0].state is (
        CoverSourceState.TIMEOUT if declared else CoverSourceState.ABSENT
    )
    assert sources[0].timeout_scope == (
        "not_started_due_to_aggregate_deadline" if declared else None
    )


@pytest.mark.parametrize(
    "terminal,url,expected",
    (
        (
            CoverSourceState.UNSAFE_REMOTE,
            "https://example.com/cover.jpg",
            CoverSourceState.UNSAFE_REMOTE,
        ),
        (CoverSourceState.UNAVAILABLE, None, CoverSourceState.UNAVAILABLE),
        (None, None, CoverSourceState.ABSENT),
        (None, "https://example.com/cover.jpg", CoverSourceState.TIMEOUT),
    ),
)
def test_expired_provider_preserves_known_outcome_before_pacing_or_transport(
    terminal, url, expected, tmp_path
):
    from dataclasses import replace

    wait = Mock(return_value=True)
    descriptor = replace(
        _provider_descriptor(CoverDesignation.WORK, "work", "edition"),
        terminal_state=terminal,
        url=url,
        wait=wait,
    )
    with patch(
        "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover"
    ) as fetch:
        sources = CoverEvidenceObserver(
            _Target(None), CoverArtifactStore(tmp_path), (_CATALOGUE,)
        ).observe(
            TargetSnapshot("ebook", "Title", (), None),
            (_candidates_observation("catalogue", "edition", "work"),),
            (),
            (descriptor,),
            time.monotonic() - 1,
        )
    assert sources[0].state is expected
    assert sources[0].timeout_scope == (
        "not_started_due_to_aggregate_deadline"
        if expected is CoverSourceState.TIMEOUT
        else None
    )
    wait.assert_not_called()
    fetch.assert_not_called()


def _open_library_descriptor(
    url: str = _COVER_URL,
    kind: CoverSourceKind = CoverSourceKind.OPEN_LIBRARY,
    redirects: CoverRedirects = CoverRedirects.OPEN_LIBRARY_ARCHIVE,
) -> ProviderCoverDescriptor:
    return ProviderCoverDescriptor(
        "catalogue",
        kind,
        cover_source_id(kind, "catalogue:edition:/books/OL1M"),
        url,
        CoverProvenance("catalogue", CoverDesignation.WORK, "work-1"),
        redirects=redirects,
        user_agent=_AGENT,
    )


def _deadline() -> CoverDeadline:
    return CoverDeadline(time.monotonic() + 30, time.monotonic() + 20)


@pytest.mark.parametrize(
    ("final_url", "family"),
    (
        (_COVER_URL, CoverFinalHost.COVERS_OPEN_LIBRARY),
        (_HOP1, CoverFinalHost.ARCHIVE),
        (_HOP2, CoverFinalHost.US_ARCHIVE),
    ),
    ids=("direct", "archive_org", "datanode"),
)
def test_open_library_retrieved_cover_records_only_the_closed_host_family(
    final_url: str, family: CoverFinalHost, tmp_path: Path
) -> None:
    with patch(
        "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
        return_value=RemoteCover(final_url, "image/jpeg", _jpeg()),
    ) as fetch:
        source = CoverEvidenceObserver(
            _Target(None), CoverArtifactStore(tmp_path)
        )._remote(_open_library_descriptor(), _deadline())
    assert (source.state, source.final_host) == (CoverSourceState.RETRIEVED, family)
    assert fetch.call_args.kwargs["redirects"] is CoverRedirects.OPEN_LIBRARY_ARCHIVE
    assert fetch.call_args.kwargs["user_agent"] == _AGENT


def test_other_retrieved_provider_covers_record_no_final_host(tmp_path: Path) -> None:
    with patch(
        "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
        return_value=RemoteCover(
            "https://assets.hardcover.app/c.jpg", "image/jpeg", _jpeg()
        ),
    ) as fetch:
        source = CoverEvidenceObserver(
            _Target(None), CoverArtifactStore(tmp_path)
        )._remote(
            _open_library_descriptor(
                "https://assets.hardcover.app/c.jpg",
                CoverSourceKind.HARDCOVER,
                CoverRedirects.NONE,
            ),
            _deadline(),
        )
    assert (source.state, source.final_host) == (CoverSourceState.RETRIEVED, None)
    assert fetch.call_args.kwargs["redirects"] is CoverRedirects.NONE


def test_an_unmapped_open_library_final_host_is_an_invariant_failure(
    tmp_path: Path,
) -> None:
    store = CoverArtifactStore(tmp_path)
    with (
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
            return_value=RemoteCover(
                "https://example.com/c.jpg", "image/jpeg", _jpeg()
            ),
        ),
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.decode_cover"
        ) as decode,
        pytest.raises(CoverEvidenceError, match="^cover final host is invalid$"),
    ):
        CoverEvidenceObserver(_Target(None), store)._remote(
            _open_library_descriptor(), _deadline()
        )
    decode.assert_not_called()


def _observe_route(tmp_path: Path, *sockets: object) -> tuple[CoverSource, _Route]:
    route = _Route(*sockets)
    with (
        patch("socket.getaddrinfo", side_effect=route._lookup),
        patch("socket.create_connection", side_effect=route._connect),
        patch("ssl.create_default_context", return_value=route.context),
    ):
        source = CoverEvidenceObserver(
            _Target(None), CoverArtifactStore(tmp_path)
        )._remote(_open_library_descriptor(), _deadline())
    return source, route


@pytest.mark.parametrize(
    ("sockets", "connects"),
    (
        ((_redirect(_HOP1.replace("https://", "http://")),), 1),
        ((_redirect(_HOP1), _redirect(_HOP2.replace("https://", "http://"))), 2),
        ((_redirect(_HOP1), _redirect("https://evil.example/x.jpg")), 2),
        ((_redirect(_HOP1), _redirect(_HOP2), _redirect(_HOP2)), 3),
    ),
    ids=("http_at_hop_1", "http_at_hop_2", "off_list_at_hop_2", "third_redirect"),
)
def test_off_route_redirect_is_recorded_unsafe_remote_not_unavailable(
    sockets: tuple[object, ...],
    connects: int,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, route = _observe_route(tmp_path, *sockets)
    assert (source.state, source.final_host) == (CoverSourceState.UNSAFE_REMOTE, None)
    assert route.connects == connects
    _assert_value_free(json.dumps(source.to_data()) + "".join(capsys.readouterr()))


def test_datanode_cover_is_retrieved_and_its_evidence_is_value_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = _jpeg()
    source, route = _observe_route(
        tmp_path, _redirect(_HOP1), _redirect(_HOP2), _image(body)
    )
    assert (source.state, source.final_host) == (
        CoverSourceState.RETRIEVED,
        CoverFinalHost.US_ARCHIVE,
    )
    assert source.artifact is not None
    assert route.connects == 3
    data = json.dumps(source.to_data())
    assert '"final_host": "us.archive.org"' in data
    _assert_value_free(data + "".join(capsys.readouterr()))


def _assert_value_free(output: str) -> None:
    for value in (
        "https://",
        "http://",
        "ia800100",
        "olcovers",
        "view_archive",
        "download",
        "7-L.jpg",
        "covers.openlibrary.org/b",
        "operator@example.invalid",
        "evil.example",
    ):
        assert value not in output


# --- Edition-cover order, caps and collapse (#1380, #1778) ------------------


def _connection(name: str, enabled: bool = True) -> ProviderConnectionConfiguration:
    return ProviderConnectionConfiguration(name, True, enabled, f"/secrets/{name}")


def _book_observation(provider: str) -> ProviderObservation:
    return ProviderObservation(
        provider,
        ProviderStatus.CANDIDATES,
        (),
        (ProviderCandidate(f"{provider}-book", f"{provider}-book", ()),),
    )


def _descriptor(
    provider: str,
    identity: str,
    designation: CoverDesignation = CoverDesignation.LISTED_EDITION,
    *,
    kind: CoverSourceKind = CoverSourceKind.HARDCOVER,
    order: int = 0,
    url: str | None = None,
) -> ProviderCoverDescriptor:
    return ProviderCoverDescriptor(
        provider,
        kind,
        cover_source_id(kind, f"{provider}:{identity}"),
        url,
        CoverProvenance(
            provider,
            designation,
            f"{provider}-book",
            None
            if designation in {CoverDesignation.DEFAULT, CoverDesignation.WORK}
            else f"{provider}-{identity}",
        ),
        order=order,
    )


def _adapter_truncation(
    provider: str, reason: CoverTruncationReason
) -> ProviderCoverDescriptor:
    return ProviderCoverDescriptor(
        provider,
        CoverSourceKind.HARDCOVER,
        cover_source_id(
            CoverSourceKind.HARDCOVER, f"truncation:{provider}:{reason.value}"
        ),
        None,
        CoverProvenance(
            provider, CoverDesignation.TRUNCATION, truncation_reason=reason
        ),
        terminal_state=CoverSourceState.TRUNCATED,
    )


def _admit(
    connections: tuple[ProviderConnectionConfiguration, ...],
    observations: tuple[ProviderObservation, ...],
    descriptors: tuple[ProviderCoverDescriptor, ...],
) -> tuple[dict[str, tuple[ProviderCoverDescriptor, ...]], tuple[CoverSource, ...]]:
    with tempfile.TemporaryDirectory() as directory:
        return CoverEvidenceObserver(
            _Target(None), CoverArtifactStore(Path(directory)), connections
        )._admit_provider_descriptors(observations, descriptors)


def _reasons(sources: tuple[CoverSource, ...], provider: str) -> set[object]:
    return {
        value.provenance.truncation_reason
        for value in sources
        if value.state is CoverSourceState.TRUNCATED
        and value.provenance is not None
        and value.provenance.provider == provider
    }


def test_admission_orders_by_designation_then_adapter_order_then_source_id() -> None:
    listed = tuple(
        _descriptor("hardcover", f"listed-{index}", order=9 - index)
        for index in range(9)
    )
    descriptors = (
        *listed,
        _descriptor("hardcover", "default", CoverDesignation.DEFAULT),
        _descriptor("hardcover", "exact", CoverDesignation.EXACT_EDITION),
    )
    admitted, truncations = _admit(
        (_connection("hardcover"),), (_book_observation("hardcover"),), descriptors
    )

    assert [
        "exact_edition",
        "default",
        *(["listed_edition"] * 6),
    ] == [
        value.provenance.designation.value  # type: ignore[union-attr]
        for value in admitted["hardcover"]
    ]
    assert [1, 2, 3, 4, 5, 6] == [value.order for value in admitted["hardcover"][2:]]
    assert {CoverTruncationReason.PROVIDER_LIMIT} == _reasons(truncations, "hardcover")


def test_a_hardcover_connection_takes_its_fifth_to_eighth_in_a_second_pass() -> None:
    descriptors = (
        *(
            _descriptor(
                "alpha",
                f"w-{index}",
                CoverDesignation.WORK,
                kind=CoverSourceKind.INVENTAIRE,
            )
            for index in range(5)
        ),
        *(_descriptor("hardcover", f"listed-{index}") for index in range(9)),
    )
    admitted, truncations = _admit(
        (_connection("alpha"), _connection("hardcover")),
        (_book_observation("alpha"), _book_observation("hardcover")),
        descriptors,
    )

    assert (4, 8) == (len(admitted["alpha"]), len(admitted["hardcover"]))
    assert {CoverTruncationReason.PROVIDER_LIMIT} == _reasons(truncations, "alpha")
    assert {CoverTruncationReason.PROVIDER_LIMIT} == _reasons(truncations, "hardcover")


def test_every_connections_first_four_come_before_hardcovers_extra_covers() -> None:
    descriptors = (
        *(
            _descriptor(
                "alpha",
                f"w-{index}",
                CoverDesignation.WORK,
                kind=CoverSourceKind.INVENTAIRE,
            )
            for index in range(4)
        ),
        *(_descriptor("hardcover", f"listed-{index}") for index in range(8)),
        *(
            _descriptor(
                "zeta",
                f"w-{index}",
                CoverDesignation.WORK,
                kind=CoverSourceKind.OPEN_LIBRARY,
            )
            for index in range(4)
        ),
    )
    admitted, truncations = _admit(
        (_connection("alpha"), _connection("hardcover"), _connection("zeta")),
        tuple(_book_observation(name) for name in ("alpha", "hardcover", "zeta")),
        descriptors,
    )

    assert (4, 4, 4) == tuple(
        len(admitted[name]) for name in ("alpha", "hardcover", "zeta")
    )
    assert {CoverTruncationReason.GALLERY_LIMIT} == _reasons(truncations, "hardcover")
    assert set() == _reasons(truncations, "zeta")


def test_an_adapter_truncation_passes_through_without_a_slot_or_work_match() -> None:
    descriptors = (
        *(_descriptor("hardcover", f"listed-{index}") for index in range(8)),
        _adapter_truncation("hardcover", CoverTruncationReason.EDITION_LIMIT),
        _adapter_truncation(
            "hardcover", CoverTruncationReason.EDITION_FETCH_INCOMPLETE
        ),
    )
    admitted, truncations = _admit(
        (_connection("hardcover"),), (_book_observation("hardcover"),), descriptors
    )

    assert 8 == len(admitted["hardcover"])
    assert {
        CoverTruncationReason.EDITION_LIMIT,
        CoverTruncationReason.EDITION_FETCH_INCOMPLETE,
    } == _reasons(truncations, "hardcover")
    assert {CoverSourceState.TRUNCATED} == {value.state for value in truncations}
    disabled, refused = _admit(
        (_connection("hardcover", enabled=False),),
        (_book_observation("hardcover"),),
        descriptors,
    )
    assert ({}, ()) == (disabled, refused)


def _measured(
    provider: str,
    identity: str,
    width: int,
    height: int,
    phash: str = "0" * 16,
    *,
    designation: CoverDesignation = CoverDesignation.LISTED_EDITION,
    measured: bool = True,
) -> CoverSource:
    measures = (
        CoverMeasures(phash, "0" * 16, (1,) + (0,) * 63, (128,) * 48)
        if measured
        else None
    )
    return CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, f"{provider}:{identity}"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(
            f"{provider}-{identity}".encode(),
            b"preview",
            "image/png",
            width,
            height,
            measures=measures,
        ),
        provenance=CoverProvenance(
            provider,
            designation,
            f"{provider}-book",
            None
            if designation is CoverDesignation.DEFAULT
            else f"{provider}-{identity}",
        ),
    )


def test_the_same_art_collapse_keeps_the_larger_listed_cover() -> None:
    small = _measured("hardcover", "a", 400, 600)
    large = _measured("hardcover", "b", 600, 900)
    other = _measured("hardcover", "c", 500, 750, "f" * 16)
    kept, truncations = CoverEvidenceObserver._collapse_same_art([small, large, other])

    assert [large, other] == kept
    assert {CoverTruncationReason.SAME_ART} == _reasons(truncations, "hardcover")


def test_the_same_art_collapse_needs_measures_and_one_listed_connection() -> None:
    cases = (
        (
            _measured("hardcover", "a", 400, 600, measured=False),
            _measured("hardcover", "b", 600, 900),
        ),
        (_measured("hardcover", "a", 400, 600), _measured("other", "b", 600, 900)),
        (
            _measured("hardcover", "a", 400, 600, designation=CoverDesignation.DEFAULT),
            _measured("hardcover", "b", 600, 900),
        ),
    )
    for sources in cases:
        kept, truncations = CoverEvidenceObserver._collapse_same_art(list(sources))
        assert list(sources) == kept
        assert () == truncations


def test_a_full_hardcover_gallery_records_every_truncation_reason() -> None:
    connections = (_connection("alpha"), _connection("beta"), _connection("hardcover"))
    descriptors = (
        *(
            _descriptor(
                "alpha",
                f"w-{index}",
                CoverDesignation.WORK,
                kind=CoverSourceKind.OPEN_LIBRARY,
            )
            for index in range(4)
        ),
        *(
            _descriptor(
                "beta",
                f"w-{index}",
                CoverDesignation.WORK,
                kind=CoverSourceKind.INVENTAIRE,
            )
            for index in range(4)
        ),
        *(
            _descriptor(
                "hardcover",
                f"listed-{index}",
                url=f"https://assets.hardcover.app/editions/{index}.jpg",
                order=index,
            )
            for index in range(10)
        ),
        _adapter_truncation("hardcover", CoverTruncationReason.EDITION_LIMIT),
        _adapter_truncation(
            "hardcover", CoverTruncationReason.EDITION_FETCH_INCOMPLETE
        ),
    )
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "mechanical_librarian.adapters.cover_evidence_observer.fetch_remote_cover",
            return_value=RemoteCover(
                "https://assets.hardcover.app/editions/0.jpg", "image/jpeg", _jpeg()
            ),
        ),
    ):
        values = CoverEvidenceObserver(
            _Target(None), CoverArtifactStore(Path(directory)), connections
        ).observe(
            TargetSnapshot("ebook", "Title", (), None),
            tuple(_book_observation(name) for name in ("alpha", "beta", "hardcover")),
            (),
            descriptors,
        )

    assert set(CoverTruncationReason) == _reasons(values, "hardcover")
    assert 1 == sum(
        value.state is CoverSourceState.RETRIEVED
        and value.provenance is not None
        and value.provenance.provider == "hardcover"
        for value in values
    )
    assert publication_ready(values)
