from __future__ import annotations

import json
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from scripts.qualify_audiobookshelf_field_effect import (
    QualificationError,
    _compose_review_client,
    _epub,
    _installed_consent_grants,
    _installed_mutation_headers,
    _operator_epub,
    _qualification_hardcover_client,
    _read_installed_return_lineage,
    _require_installed_full_chain_coverage,
    _require_installed_lifecycle_evidence,
    _require_installed_return_automatic_transition,
    _require_installed_return_lineage_progress,
)

from mechanical_librarian.interfaces.web.react_assets import ReactAssetRecovery


def test_installed_mutations_bind_the_loaded_browser_identity() -> None:
    assets = SimpleNamespace(build_identity="a" * 64)
    with patch(
        "scripts.qualify_audiobookshelf_field_effect.ReactAssetLoader"
    ) as loader:
        loader.return_value.load.return_value = assets
        headers = _installed_mutation_headers(
            {"Origin": "https://review.test"}, "csrf-token"
        )

    assert headers == {
        "Origin": "https://review.test",
        "X-CSRF-Token": "csrf-token",
        "X-ML-Build-Identity": "a" * 64,
    }


def test_installed_mutations_fail_closed_without_browser_assets() -> None:
    with patch(
        "scripts.qualify_audiobookshelf_field_effect.ReactAssetLoader"
    ) as loader:
        loader.return_value.load.return_value = ReactAssetRecovery()
        with pytest.raises(
            QualificationError, match="installed_browser_assets_unavailable"
        ):
            _installed_mutation_headers({"Origin": "https://review.test"}, "csrf-token")


def test_installed_consent_uses_only_capability_allowed_destinations() -> None:
    grants = _installed_consent_grants(
        {
            "capabilities": [
                {
                    "library": "alpha",
                    "destinations": [
                        {"destination": "description", "state": "not_ready"},
                        {"destination": "title", "state": "allowed"},
                    ],
                },
                {
                    "library": "beta",
                    "destinations": [
                        {"destination": "authors", "state": "allowed"},
                    ],
                },
            ]
        },
        (("alpha", "alpha-id", Path("alpha")), ("beta", "beta-id", Path("beta"))),
    )

    assert grants == [
        {"library": "alpha", "destinations": ["title"]},
        {"library": "beta", "destinations": ["authors"]},
    ]


def test_installed_qualification_composes_application_before_fastapi() -> None:
    repository = Mock()
    review = SimpleNamespace(
        owner_password_hash_file="/synthetic/owner.hash",
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=5,
    )
    preparation = Mock()
    continuous = Mock()
    cover_considerations = Mock()
    application = object()
    client = object()

    with (
        patch(
            "scripts.qualify_audiobookshelf_field_effect.load_owner_password_verifier",
            return_value=lambda _: False,
        ),
        patch(
            "scripts.qualify_audiobookshelf_field_effect.compose_review_application",
            return_value=application,
        ) as compose,
        patch(
            "scripts.qualify_audiobookshelf_field_effect.composition._compose_cover_considerations",
            return_value=cover_considerations,
        ) as compose_covers,
        patch(
            "scripts.qualify_audiobookshelf_field_effect.create_review_app",
            return_value=object(),
        ) as create,
        patch(
            "scripts.qualify_audiobookshelf_field_effect.TestClient",
            return_value=client,
        ),
    ):
        result = _compose_review_client(
            repository,
            review,
            preparation,
            continuous,
            (("books", None),),
            Path("/synthetic/artifacts"),
        )

    assert result is client
    compose.assert_called_once()
    assert compose.call_args.kwargs["cover_considerations"] is cover_considerations
    compose_covers.assert_called_once_with(
        repository, Path("/synthetic/artifacts"), review
    )
    create.assert_called_once_with(application, review)


@pytest.mark.parametrize(
    ("status", "candidate"),
    (
        (202, SimpleNamespace(scan_id="review_scan", updated_at=11)),
        (204, SimpleNamespace(scan_id="review_scan", updated_at=10)),
        (204, SimpleNamespace(scan_id="other", updated_at=11)),
    ),
    ids=("unaccepted_status", "unchanged_scan", "foreign_scan"),
)
def test_return_to_automatic_retains_manual_hold_until_fresh_lineage(
    status: int, candidate: SimpleNamespace
) -> None:
    before = SimpleNamespace(scan_id="review_scan", updated_at=10)
    after = SimpleNamespace(scan_id="review_scan", updated_at=11)
    _require_installed_return_automatic_transition(204, before, after)

    with pytest.raises(QualificationError, match="installed_return_automatic_failed"):
        _require_installed_return_automatic_transition(status, before, candidate)


@pytest.mark.parametrize(
    ("state", "rescan_requested", "updated_at_offset", "hold"),
    (
        ("processing", True, 0, None),
        ("completed", False, -1, None),
        ("completed_with_failures", False, 1, object()),
    ),
    ids=("still_processing", "stale_completion", "completed_with_failures"),
)
def test_return_to_automatic_requires_requested_scan_before_hold_clear(
    state: str, rescan_requested: bool, updated_at_offset: int, hold: object | None
) -> None:
    requested_scan_id = "scan-library"
    requested_at = 20
    assert not _require_installed_return_lineage_progress(
        SimpleNamespace(
            scan_id=requested_scan_id,
            state="processing",
            rescan_requested=True,
            updated_at=requested_at,
        ),
        requested_scan_id,
        requested_at,
        object(),
    )
    assert _require_installed_return_lineage_progress(
        SimpleNamespace(
            scan_id=requested_scan_id,
            state="completed",
            rescan_requested=False,
            updated_at=requested_at + 1,
        ),
        requested_scan_id,
        requested_at,
        None,
    )
    assert _require_installed_return_lineage_progress(
        SimpleNamespace(
            scan_id=requested_scan_id,
            state="completed",
            rescan_requested=False,
            updated_at=requested_at + 1,
        ),
        requested_scan_id,
        requested_at,
        object(),
    )
    progress = SimpleNamespace(
        scan_id=requested_scan_id,
        state=state,
        rescan_requested=rescan_requested,
        updated_at=requested_at + updated_at_offset,
    )
    with pytest.raises(
        QualificationError, match="installed_return_fresh_lineage_failed"
    ):
        _require_installed_return_lineage_progress(
            progress, requested_scan_id, requested_at, hold
        )


def test_return_lineage_reads_hold_before_progress() -> None:
    events = []
    repository = SimpleNamespace(
        field_effect_manual_hold_context=(
            lambda *_: events.append("hold") or None  # type: ignore[func-returns-value]  # reason: deliberate `append(...) or value` idiom - `append` returning `None` is what lets the following value be the fake's result while the call order is recorded.
        ),
        review_pipeline_progress=(
            lambda *_: (
                events.append("progress")
                or SimpleNamespace(  # type: ignore[func-returns-value]  # reason: deliberate `append(...) or value` idiom - `append` returning `None` is what lets the following value be the fake's result while the call order is recorded.
                    state="completed"
                )
            )
        ),
    )

    progress, hold = _read_installed_return_lineage(
        repository, "primary", "books", "review_case", "title"
    )

    assert events == ["hold", "progress"]
    assert progress.state == "completed"
    assert hold is None


def test_hardcover_fixture_uses_distinct_candidate_identities_per_discovery_lane() -> (
    None
):
    client = _qualification_hardcover_client(
        {}, "synthetic-provider-token-a", {}, {}, {}
    )
    try:
        isbn_response = client.post(
            "https://qualification.invalid/graphql",
            json={"variables": {"isbns": ["9780306406157"]}},
        )
        work_response = client.post(
            "https://qualification.invalid/graphql",
            json={
                "variables": {
                    "title": "Guarded qualification title",
                    "authors": ["Synthetic Author"],
                }
            },
        )
    finally:
        client.close()

    isbn_data = json.loads(isbn_response.content)["data"]
    work_data = json.loads(work_response.content)["data"]
    assert isbn_data["editions"][0]["id"] != work_data["editions"][0]["id"]
    assert isbn_data["books"][0]["id"] != work_data["books"][0]["id"]


def test_missing_installed_destination_fails_full_chain_qualification() -> None:
    expected = {"title": "automatic_policy", "cover": "human"}
    with pytest.raises(
        QualificationError, match="installed_scheduler_failed"
    ) as captured:
        _require_installed_full_chain_coverage(
            {"title": "automatic_policy"},
            expected,
            activity_present=True,
            diagnostics={"thread_alive": False},
        )
    assert '"missing": {"cover": "human"}' in str(captured.value)

    _require_installed_full_chain_coverage(
        expected,
        expected,
        activity_present=True,
        diagnostics={},
    )


def test_lifecycle_evidence_reads_cleanup_from_persistence_not_public_activity() -> (
    None
):
    _require_installed_lifecycle_evidence(
        activity_status=200,
        suspensions=({"scope": "destination"},),
        pending_cleanup_plan_ids=(),
        event_documents=(("planned",), ("verified",)),
    )
    with pytest.raises(QualificationError, match="installed_lifecycle_evidence_failed"):
        _require_installed_lifecycle_evidence(
            activity_status=200,
            suspensions=({"scope": "destination"},),
            pending_cleanup_plan_ids=("effect_plan_pending",),
            event_documents=(("planned",),),
        )


def test_operator_overlay_preserves_content_and_binds_valid_isbn_metadata() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.epub"
        destination = root / "operator.epub"
        _epub(source)
        isbn, title, author = _operator_epub(source, destination, 3)

        assert 13 == len(isbn)
        assert isbn.isdigit()
        assert (
            0
            == sum(
                int(value) * (1 if position % 2 == 0 else 3)
                for position, value in enumerate(isbn)
            )
            % 10
        )
        with (
            zipfile.ZipFile(source) as original,
            zipfile.ZipFile(destination) as overlaid,
        ):
            assert original.read("OEBPS/chapter.xhtml") == overlaid.read(
                "OEBPS/chapter.xhtml"
            )
            package = ET.fromstring(overlaid.read("OEBPS/content.opf"))
            metadata = package.find("{http://www.idpf.org/2007/opf}metadata")
            assert metadata is not None
            dc = "{http://purl.org/dc/elements/1.1/}"
            assert title == metadata.findtext(dc + "title")
            assert isbn == metadata.findtext(dc + "identifier")
        assert "Synthetic Author." == author
        assert 1 == len(metadata.findall(dc + "creator"))
