from __future__ import annotations

import json
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import scripts.qualify_audiobookshelf_field_effect as qualification
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

from mechanical_librarian.application.field_effects import authority_from_decision
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.infrastructure.configuration import ConfigurationInput
from mechanical_librarian.interfaces.web.react_assets import ReactAssetRecovery


class _ConfigurationAdmitted(Exception):
    pass


def test_generated_field_effect_configuration_admits_the_current_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "books"
    library.mkdir()

    def admit(path: Path) -> None:
        document = json.loads(path.read_text(encoding="utf-8"))
        admitted = ConfigurationInput.model_validate(document)
        assert admitted.schema_version == "config-v8"
        raise _ConfigurationAdmitted

    monkeypatch.setattr(qualification, "load_configuration_file", admit)

    with pytest.raises(_ConfigurationAdmitted):
        qualification._installed_continuous_qualification(
            tmp_path,
            "https://127.0.0.1:9",
            "synthetic-writer-key",
            (("books", "01234567-89ab-4def-8123-456789abcdef", library),),
            "drift-item",
            {},
            set(),
            {},
            {},
            set(),
        )


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


def test_installed_qualification_composes_application_before_fastapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    repository = Mock()
    review = SimpleNamespace(
        owner_password_hash_file="/synthetic/owner.hash",
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=5,
    )
    preparation = Mock()
    reviewer_access = Mock()
    continuous = Mock()
    cover_considerations = Mock()
    application = object()
    client = object()

    with (
        patch(
            "scripts.qualify_audiobookshelf_field_effect.load_owner_password_binding",
            return_value=(lambda _: False, lambda _: "d" * 64),
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
            reviewer_access,
            preparation,
            continuous,
            (("books", None),),
            Path("/synthetic/artifacts"),
        )

    assert result is client
    assert compose.call_args.kwargs["verify_password"] is not None
    assert compose.call_args.kwargs["session_digest"]("token") == "d" * 64
    compose.assert_called_once()
    assert compose.call_args.kwargs["cover_considerations"] is cover_considerations
    assert compose.call_args.kwargs["reviewer_access"] is reviewer_access
    compose_covers.assert_called_once_with(
        repository, Path("/synthetic/artifacts"), review, reviewer_access
    )
    create.assert_called_once_with(application, review)
    assert list(tmp_path.iterdir()) == []


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
                events.append("progress")  # type: ignore[func-returns-value]  # reason: deliberate `append(...) or value` idiom - `append` returning `None` is what lets the following value be the fake's result while the call order is recorded.
                or SimpleNamespace(state="completed")
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


def test_missing_persisted_automatic_series_authority_fails_qualification() -> None:
    with pytest.raises(QualificationError, match="installed_automatic_series_unproved"):
        qualification._require_installed_automatic_series_evidence(
            "supplied",
            decision=None,
            operations=(),
            before=None,
            after=[{"name": "Guarded Qualification", "sequence": "2.5"}],
            series_requests=1,
        )


@pytest.fixture
def automatic_series_decision() -> AutomaticDecision:
    return _series_decision(1)


def _series_decision(occurred_at: int) -> AutomaticDecision:
    return AutomaticDecision.create(
        review_case_id="review_case_" + "a" * 64,
        report_id="report_" + "b" * 64,
        proposal_set_id="proposal_set_" + "c" * 64,
        proposal_id="review_proposal_" + "d" * 64,
        target_fingerprint="e" * 64,
        destination="series",
        outcome="candidate",
        candidate_id="alternative_" + "f" * 64,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="series",
        policy_option="primary_narrative",
        qualification_manifest_sha256=qualification.F20_QUALIFICATION_CANDIDATE_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        occurred_at=occurred_at,
        bundle_id=None,
    )


@pytest.mark.parametrize("scenario,sequence", (("supplied", "2.5"), ("missing", None)))
def test_installed_series_requires_exact_current_decision_effect_and_readback(
    automatic_series_decision: AutomaticDecision,
    scenario: str,
    sequence: str | None,
) -> None:
    desired = [{"name": "Guarded Qualification", "sequence": sequence}]
    authority = authority_from_decision(automatic_series_decision)
    assert authority is not None
    plan = FieldEffectPlan.create(
        connection="canary",
        library="series",
        work_id="item",
        destination="series",
        pre_value=None,
        desired_value=desired,
        target_fingerprint="e" * 64,
        target_version="version",
        dependency_digests=("d" * 64,),
        authority=authority,
        consent=EffectConsent.create(
            connection="canary",
            library="series",
            destination="series",
            envelope_identity="e" * 64,
            granted_at=1,
        ),
        capability_digest="f" * 64,
        created_at=1,
    )
    operation = FieldEffectOperation(plan, state="verified", attempt=1, updated_at=2)
    qualification._require_installed_automatic_series_evidence(
        scenario,
        decision=automatic_series_decision,
        operations=(operation,),
        before=None,
        after=desired,
        series_requests=1,
    )

    def assert_unproved(
        *,
        decision: AutomaticDecision | None = automatic_series_decision,
        operations: tuple[FieldEffectOperation, ...] = (operation,),
        before: object = None,
        after: object = desired,
        series_requests: int = 1,
    ) -> None:
        with pytest.raises(
            QualificationError, match="installed_automatic_series_unproved"
        ):
            qualification._require_installed_automatic_series_evidence(
                scenario,
                decision=decision,
                operations=operations,
                before=before,
                after=after,
                series_requests=series_requests,
            )

    assert_unproved(operations=())
    assert_unproved(operations=(replace(operation, state="planned"),))
    assert_unproved(series_requests=0)
    assert_unproved(series_requests=2)
    assert_unproved(before=[{"name": "Existing", "sequence": None}])
    assert_unproved(after=[{"name": "Guarded Qualification", "sequence": "1"}])
    assert_unproved(decision=None)
    assert_unproved(decision=_series_decision(2))
    with pytest.raises(QualificationError, match="installed_automatic_series_unproved"):
        qualification._require_installed_automatic_series_evidence(
            "wrong_author",
            decision=None,
            operations=(operation,),
            before=None,
            after=None,
            series_requests=0,
        )


def test_wrong_author_refusal_requires_no_candidate_plan_or_series_request(
    automatic_series_decision: AutomaticDecision,
) -> None:
    qualification._require_installed_automatic_series_evidence(
        "wrong_author",
        decision=None,
        operations=(),
        before=None,
        after=None,
        series_requests=0,
    )

    def assert_unproved(
        *,
        decision: AutomaticDecision | None = None,
        after: object = None,
        series_requests: int = 0,
    ) -> None:
        with pytest.raises(
            QualificationError, match="installed_automatic_series_unproved"
        ):
            qualification._require_installed_automatic_series_evidence(
                "wrong_author",
                decision=decision,
                operations=(),
                before=None,
                after=after,
                series_requests=series_requests,
            )

    assert_unproved(decision=automatic_series_decision)
    assert_unproved(series_requests=1)
    assert_unproved(after=[{"name": "Guarded Qualification", "sequence": None}])


@pytest.mark.parametrize("scenario", ("supplied", "missing", "wrong_author"))
@pytest.mark.parametrize("lane", ("isbn", "work"))
def test_installed_series_provider_controls_retain_number_and_author_boundaries(
    scenario: str,
    lane: str,
) -> None:
    title = "Synthetic series control"
    with _qualification_hardcover_client(
        {},
        "synthetic-provider-token-a",
        {},
        {"9780140328721": title},
        {},
        series_scenarios={title: scenario},
    ) as client:
        response = client.post(
            "https://qualification.invalid/graphql",
            json={
                "variables": {"isbns": ["9780140328721"]}
                if lane == "isbn"
                else {
                    "title": title,
                    "authors": ["Synthetic Author", "Second Qualified Author"],
                }
            },
        )
    data = response.json()["data"]
    assert data["editions"][0]["isbn_13"] == "9780140328721"
    book = data["books"][0]
    assert book["title"] == title
    assert book["book_series"][0]["position"] == (
        None if scenario == "missing" else "2.5"
    )
    assert [value["author"]["name"] for value in book["contributions"]] == (
        ["Unrelated Author"]
        if scenario == "wrong_author"
        else ["Synthetic Author", "Second Qualified Author"]
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
