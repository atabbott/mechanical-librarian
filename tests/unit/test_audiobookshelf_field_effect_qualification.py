from __future__ import annotations

import ast
import json
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
import scripts.qualify_audiobookshelf_field_effect as qualification
from scripts.qualify_audiobookshelf_field_effect import (
    QualificationError,
    _audiobook,
    _audiobook_canary,
    _compose_review_client,
    _epub,
    _installed_accept_payload,
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

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.transactions import TransactionScope
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    CONFIG_SCHEMA,
    ApplicationConfiguration,
    LibraryConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import authority_from_decision
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
)
from mechanical_librarian.application.operation_admission import OperationAdmission
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.human_decision import validate_cover_confirmation
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    ProviderQueryKind,
    ProviderStatus,
    isbn_family,
)
from mechanical_librarian.infrastructure.configuration import ConfigurationInput
from mechanical_librarian.interfaces.web.app import _DecisionRequest
from mechanical_librarian.interfaces.web.react_assets import ReactAssetRecovery


class _ConfigurationAdmitted(Exception):
    pass


def test_series_request_observer_uses_real_configured_secret_file_factory(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "target.secret"
    secret.write_text("synthetic-secret", encoding="ascii")
    secret.chmod(0o600)
    configuration = ApplicationConfiguration(
        (TargetConnectionConfiguration("canary", "http://127.0.0.1:9", str(secret)),),
        (LibraryConfiguration("series", "canary", "library", "book", None, ()),),
        (),
        (),
    )
    artifacts = tmp_path / "artifacts"
    qualification.CoverArtifactStore(artifacts)
    requests: list[str] = []
    observed_authorization: list[str] = []

    def respond(
        _transport: httpx.HTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        observed_authorization.append(request.headers["Authorization"])
        return httpx.Response(200, json={"updated": True})

    with (
        patch.object(httpx.HTTPTransport, "handle_request", respond),
        qualification._observe_installed_series_requests(requests),
    ):
        target = qualification.composition.compose_audiobookshelf_field_effect_target(
            configuration, artifacts, "canary", "series"
        )
        with target._client_factory() as client:
            client.patch(
                "/api/items/item/media",
                json={
                    "metadata": {
                        "series": [{"name": "Guarded Qualification", "sequence": None}]
                    }
                },
            )
            client.patch(
                "/api/items/item/media",
                json={"metadata": {"title": "Unchanged sentinel"}},
            )
    assert observed_authorization == [
        "Bearer synthetic-secret",
        "Bearer synthetic-secret",
    ]
    assert requests == ["item"]


@pytest.mark.parametrize("operation", ("suspend", "count"))
def test_installed_observation_and_suspension_use_the_composed_scheduler_gate(
    tmp_path: Path,
    operation: str,
) -> None:
    repository = qualification.open_repository(
        tmp_path / "application.sqlite3", cross_thread=True
    )
    continuous = ContinuousGuardedApplication(
        repository,
        {},
        {},
        PersistedFieldEffectAuthority(repository),
        (("canary", "books"),),
        lambda: 1,
        lambda _connection, _library: None,
        activity_only=True,
    )
    gate = threading.RLock()
    gate_entered = threading.Event()
    errors: list[BaseException] = []
    counts: list[tuple[int, int]] = []

    def serialized(call: Callable[[], object]) -> object:
        gate_entered.set()
        with gate:
            return call()

    continuous.attach_serialization(serialized)

    def suspend() -> None:
        try:
            if operation == "suspend":
                qualification._suspend_installed_cover_capability(
                    repository, continuous
                )
            else:
                counts.append(
                    qualification._installed_current_case_count(repository, continuous)
                )
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=suspend)
    try:
        with gate:
            transaction = TransactionScope(repository._connection, "scheduler_control")
            transaction.begin()
            worker.start()
            try:
                assert gate_entered.wait(1), (
                    "fixture operation bypassed scheduler serialization"
                )
                assert (
                    repository.field_effect_capability_suspension("canary", "cover")
                    is None
                )
                assert not counts
                assert repository._connection.in_transaction
            finally:
                transaction.commit()
        worker.join(2)
        assert not worker.is_alive()
        assert not errors
        if operation == "suspend":
            assert (
                repository.field_effect_capability_suspension("canary", "cover")
                is not None
            )
        else:
            assert counts == [(0, 0)]
            assert (
                repository.field_effect_capability_suspension("canary", "cover") is None
            )
        assert not repository._connection.in_transaction
    finally:
        worker.join(2)
        repository.close()


def test_generated_field_effect_configuration_admits_the_current_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "books"
    library.mkdir()

    def admit(path: Path) -> None:
        document = json.loads(path.read_text(encoding="utf-8"))
        admitted = ConfigurationInput.model_validate(document)
        assert admitted.schema_version == CONFIG_SCHEMA
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
    admission = object()
    operation_lock = object()
    repository.operation_admission = admission
    repository.operation_lock = operation_lock
    review = SimpleNamespace(
        owner_password_hash_file="/synthetic/owner.hash",
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=5,
    )
    preparation = Mock()
    reviewer_access = Mock()
    continuous = Mock()
    activity_projection = Mock()
    close_activity_projection = Mock()
    cover_considerations = Mock()
    application = object()
    client = object()

    with (
        patch(
            "scripts.qualify_audiobookshelf_field_effect.load_owner_password_binding",
            return_value=(lambda _: False, lambda _: "d" * 64, lambda _: "e" * 64),
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
            activity_projection,
            close_activity_projection,
        )

    assert result is client
    assert compose.call_args.kwargs["verify_password"] is not None
    assert compose.call_args.kwargs["session_digest"]("token") == "d" * 64
    compose.assert_called_once()
    assert compose.call_args.kwargs["cover_considerations"] is cover_considerations
    assert compose.call_args.kwargs["reviewer_access"] is reviewer_access
    # The review application queues at the root repository's admission and
    # gate, as the product composes it, never a private pair (#1561).
    assert compose.call_args.kwargs["admission"] is admission
    assert compose.call_args.kwargs["operation_lock"] is operation_lock
    assert compose.call_args.kwargs["activity_projection"] is activity_projection
    compose.call_args.kwargs["close_activity_projection"]()
    close_activity_projection.assert_called_once_with()
    compose_covers.assert_called_once_with(
        repository, Path("/synthetic/artifacts"), review, reviewer_access
    )
    create.assert_called_once_with(application, review)
    assert list(tmp_path.iterdir()) == []


def test_installed_root_repository_opens_with_a_fresh_shared_admission(
    tmp_path: Path,
) -> None:
    database = tmp_path / "installed-application.sqlite3"
    first = qualification._open_root_repository(database)
    try:
        first_admission = first.operation_admission
        assert isinstance(first_admission, OperationAdmission)
    finally:
        first.close()
    reopened = qualification._open_root_repository(database, create=False)
    try:
        assert isinstance(reopened.operation_admission, OperationAdmission)
        assert reopened.operation_admission is not first_admission
    finally:
        reopened.close()


# Each installed phase and how many times it opens its root repository (the
# continuous phase reopens it to prove restart recovery).
_INSTALLED_PHASES = {
    "_installed_continuous_qualification": 2,
    "_installed_automatic_series_qualification": 1,
}


def _shared_admission_violations(module: ast.Module) -> list[str]:
    """Name every installed-phase composition that bypasses the root admission."""
    violations: list[str] = []

    def keyword_source(call: ast.Call, name: str) -> str | None:
        for keyword in call.keywords:
            if (
                keyword.arg == name
                and isinstance(keyword.value, ast.Attribute)
                and isinstance(keyword.value.value, ast.Name)
            ):
                return keyword.value.value.id + "." + keyword.value.attr
        return None

    functions = {
        node.name: node for node in module.body if isinstance(node, ast.FunctionDef)
    }
    for phase, expected_opens in _INSTALLED_PHASES.items():
        opens = 0
        pipelines = 0
        for node in ast.walk(functions[phase]):
            if not isinstance(node, ast.Call):
                continue
            called = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if called == "open_repository":
                violations.append(phase + ": bare open_repository")
            if called == "_open_root_repository":
                opens += 1
            if called == "create_review_pipeline":
                pipelines += 1
                source = keyword_source(node, "admission")
                if source is None or not source.endswith(".operation_admission"):
                    violations.append(phase + ": pipeline without root admission")
        # A refactor that moves composition out of the phase must move this
        # guard with it, never leave it checking nothing.
        if opens != expected_opens:
            violations.append(phase + ": root opens moved")
        if pipelines != 1:
            violations.append(phase + ": pipeline composition moved")
    client = functions["_compose_review_client"]
    composes = [
        node
        for node in ast.walk(client)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "compose_review_application"
    ]
    if not composes:
        violations.append("_compose_review_client: no review application")
    for call in composes:
        if keyword_source(call, "admission") != "repository.operation_admission":
            violations.append("_compose_review_client: private admission")
        if keyword_source(call, "operation_lock") != "repository.operation_lock":
            violations.append("_compose_review_client: private operation lock")
    return violations


def test_installed_phases_compose_around_the_root_admission() -> None:
    source = Path(qualification.__file__).read_text(encoding="utf-8")
    assert _shared_admission_violations(ast.parse(source)) == []

    # Negative controls: dropping either keyword is caught.
    for removed in ("admission", "operation_lock"):
        module = ast.parse(source)
        for node in ast.walk(module):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id
                in {"compose_review_application", "create_review_pipeline"}
            ):
                node.keywords = [k for k in node.keywords if k.arg != removed]
        assert _shared_admission_violations(module) != []

    # A root open that bypasses the admission helper is caught too.
    module = ast.parse(source)
    for node in ast.walk(module):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_open_root_repository"
        ):
            node.func.id = "open_repository"
            break
    assert _shared_admission_violations(module) != []


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
    client = _qualification_hardcover_client({}, {}, {}, {})
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


def test_hardcover_fixture_answers_each_isbn_family_on_its_own() -> None:
    # #1545: a real book carries opening-matter ISBNs beside the one the canary
    # injects, so discovery asks one lane per family. One record answering for
    # two ISBNs is an identity conflict that drops every lane, and an operator
    # catalogue that answered an ISBN it does not hold would split the work's
    # evidence.
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed=value,
            canonical=value,
            excerpt="ISBN: " + value,
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
        for value in ("9780306406157", "9780140328721")
    )
    lanes = tuple(
        MetadataDiscoveryLane(
            "lane_" + str(index) * 64,
            ProviderQueryKind.ISBN,
            (claim.reference,),
            isbn_family(claim.canonical),
            (str(claim.canonical),),
        )
        for index, claim in enumerate(claims, 1)
    )
    plan = MetadataDiscoveryPlan(claims, lanes)
    synthetic = _qualification_hardcover_client({}, {}, {}, {})
    operator = _qualification_hardcover_client(
        {},
        {},
        {"9780306406157": "Operator qualification 1"},
        {"9780306406157": "Operator Author."},
    )

    statuses = [
        [
            lane.status
            for lane in HardcoverMetadataProvider(
                "qualification-catalogue-a", client, 65_536
            )
            .observe(plan)
            .observation.lanes
        ]
        for client in (synthetic, operator)
    ]

    assert [
        [ProviderStatus.CANDIDATES, ProviderStatus.CANDIDATES],
        [ProviderStatus.CANDIDATES, ProviderStatus.NO_MATCH],
    ] == statuses


def test_generated_canary_catalogues_agree_with_the_generated_epub() -> None:
    # #1427: split title or author evidence is a material conflict the policy
    # keeps for the owner, so it can never prove an automatic decision.
    corrections = {"After": "Before", "Guarded qualification title": "Before"}
    with _qualification_hardcover_client(corrections, {}, {}, {}) as client:
        responses = (
            client.post(
                "https://qualification.invalid/graphql",
                json={"variables": {"isbns": ["9780306406157"]}},
            ),
            client.post(
                "https://qualification.invalid/graphql",
                json={"variables": {"title": "After", "authors": ["Seed Author"]}},
            ),
        )

    for response in responses:
        edition = json.loads(response.content)["data"]["editions"][0]
        assert edition["title"] == "Before"
        assert [value["author"]["name"] for value in edition["contributions"]] == [
            "Synthetic Author",
            "Second Qualified Author",
        ]


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


@pytest.mark.parametrize(
    ("current_selection", "match_state", "confirm"),
    [
        (None, "match_not_confirmed", True),
        ("cover_selection_" + "a" * 64, "match_confirmed", False),
    ],
)
def test_installed_cover_accept_satisfies_the_decision_contract(
    current_selection: str | None, match_state: str, confirm: bool
) -> None:
    # #1185: the canary's cover accept drifted from the contract once already.
    field: dict[str, object] = {
        "current_cover_selection_id": current_selection,
        "destination": "cover",
        "proposal_id": "review_proposal_" + "b" * 64,
        "target_fingerprint": "c" * 64,
        "offer_digest": "f" * 64,
    }
    selected_cover: dict[str, object] = {
        "artifact_id": "artifact",
        "match_state": match_state,
    }
    payload = _installed_accept_payload(
        field,
        {"kind": "cover", "value": "artifact"},
        ("cover_consideration_" + "d" * 64, selected_cover),
    )

    assert payload["cover_confirmation"] == {
        "expected_selection_id": current_selection,
        "confirm_unverified_match": confirm,
    }
    assert (
        validate_cover_confirmation(payload["cover_confirmation"])
        is (payload["cover_confirmation"])
    )
    _DecisionRequest.model_validate(payload)


def test_installed_non_cover_accept_carries_no_cover_confirmation() -> None:
    field: dict[str, object] = {
        "destination": "description",
        "proposal_id": "review_proposal_" + "b" * 64,
        "target_fingerprint": "c" * 64,
        "offer_digest": "f" * 64,
    }
    payload = _installed_accept_payload(field, {"kind": "text", "value": "x"})

    assert "cover_confirmation" not in payload
    assert "consideration_id" not in payload
    assert payload["offered_alternatives_digest"] == "f" * 64


def test_missing_persisted_automatic_series_authority_fails_qualification() -> None:
    with pytest.raises(
        QualificationError, match="installed_automatic_series_unproved"
    ) as failure:
        qualification._require_installed_automatic_series_evidence(
            "supplied",
            decision=None,
            operations=(),
            before=None,
            after=[{"name": "Guarded Qualification", "sequence": "2.5"}],
            series_requests=1,
        )
    diagnostic = json.loads(str(failure.value).split(":", 2)[2])
    assert "persisted_series_decision" in diagnostic["failed_checks"]
    assert diagnostic["series_requests"] == 1
    assert "Guarded Qualification" not in str(failure.value)


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
        authority=replace(authority, source="persisted_review_case"),
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
    assert_unproved(
        operations=(replace(operation, plan=replace(plan, authority=authority)),)
    )
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


def test_generated_audiobook_is_a_readable_m4b_carrying_the_scanned_title() -> None:
    """No binary media is committed: the canary's audiobook is written in code."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "qualification-audiobook.m4b"
        _audiobook(path)
        body = path.read_bytes()

        assert b"ftyp" == body[4:8]
        assert b"M4B " == body[8:12]
        assert qualification.AUDIOBOOK_TITLE.encode() in body
        assert b"moov" in body and b"mdat" in body


def test_the_audiobook_canary_records_every_destination_it_probed() -> None:
    """Each book-media destination is discovered and applied on the audiobook."""
    applied: list[str] = []

    def apply(*arguments: object, **_: object) -> dict[str, object]:
        destination = str(arguments[7])
        applied.append(destination)
        return {"destination": destination, "state": "verified"}

    capabilities = SimpleNamespace(
        result=lambda plan: SimpleNamespace(category="allowed")
    )
    with patch.object(qualification, "_apply", apply):
        section = _audiobook_canary(
            object(),
            object(),
            object(),
            capabilities,
            [],
            object(),
            "item-1",
            lambda: 1,
        )

    expected = [destination for destination, _ in qualification.AUDIOBOOK_DESTINATIONS]
    assert ["narrators", "series", "asin", "description", "language"] == expected
    assert expected == applied
    assert "adapter" == section["route"]
    assert "canary-gamma" == section["library"]
    assert "book" == section["item_media_type"]
    assert dict.fromkeys(expected, "allowed") == section["capabilities"]
    assert expected == [row["destination"] for row in section["outcomes"]]


def test_one_rejected_audiobook_shape_disables_only_that_destination() -> None:
    """The expansion boundary: a refusal is recorded, and the run continues.

    A destination the capability probe did not allow is never applied at all,
    and a pre-read the adapter refuses disables its own field. Both leave every
    other destination to run.
    """
    applied: list[str] = []

    def apply(*arguments: object, **_: object) -> dict[str, object]:
        destination = str(arguments[7])
        applied.append(destination)
        if destination == "asin":
            raise QualificationError("field_read_invalid_asin")
        return {"destination": destination, "state": "verified"}

    capabilities = SimpleNamespace(
        result=lambda plan: SimpleNamespace(
            category="field_unavailable" if plan.destination == "series" else "allowed"
        )
    )
    with patch.object(qualification, "_apply", apply):
        section = _audiobook_canary(
            object(),
            object(),
            object(),
            capabilities,
            [],
            object(),
            "item-1",
            lambda: 1,
        )

    outcomes = {str(row["destination"]): row for row in section["outcomes"]}
    assert 5 == len(outcomes)
    # The disallowed destination is never written to at all.
    assert "series" not in applied
    assert "field_unavailable" == outcomes["series"]["state"]
    assert "field_unavailable" == outcomes["series"]["failure"]
    assert "field_unavailable" == section["capabilities"]["series"]
    # A refused pre-read disables only its own field.
    assert "field_unavailable" == outcomes["asin"]["state"]
    assert "field_read_invalid_asin" == outcomes["asin"]["failure"]
    assert ["narrators", "description", "language"] == [
        destination
        for destination, row in outcomes.items()
        if row["state"] == "verified"
    ]


def test_an_unverified_audiobook_write_is_a_failure_not_a_disabled_field() -> None:
    """A write the service accepted but did not make must never read as history.

    `field_unavailable` says the destination is unsupported. Evidence that a
    supported write did not land is a qualification failure, so it leaves
    `_audiobook_canary` and fails the run rather than becoming a receipt row.
    """

    def apply(*arguments: object, **_: object) -> dict[str, object]:
        raise QualificationError("guarded_write_evidence_invalid")

    capabilities = SimpleNamespace(
        result=lambda plan: SimpleNamespace(category="allowed")
    )
    with (
        patch.object(qualification, "_apply", apply),
        pytest.raises(QualificationError, match="guarded_write_evidence_invalid"),
    ):
        _audiobook_canary(
            object(),
            object(),
            object(),
            capabilities,
            [],
            object(),
            "item-1",
            lambda: 1,
        )


@pytest.mark.parametrize(
    "category", ("connection_unavailable", "insufficient_permission")
)
def test_a_non_shape_capability_refusal_aborts_the_audiobook_qualification(
    category: str,
) -> None:
    """An unreachable service or an unauthorised key is a defect, not a field.

    `field_unavailable` is the one category that means "this service cannot
    carry this field's shape". Every other refusal says something about the
    run, so recording it as a disabled destination would publish a receipt
    claiming the library refused a field it was never asked about (#968).
    """
    applied: list[str] = []

    def apply(*arguments: object, **_: object) -> dict[str, object]:
        applied.append(str(arguments[7]))
        return {"destination": str(arguments[7]), "state": "verified"}

    capabilities = SimpleNamespace(
        result=lambda plan: SimpleNamespace(
            category=category if plan.destination == "series" else "allowed"
        )
    )
    with (
        patch.object(qualification, "_apply", apply),
        pytest.raises(QualificationError, match="audiobook_capability_" + category),
    ):
        _audiobook_canary(
            object(),
            object(),
            object(),
            capabilities,
            [],
            object(),
            "item-1",
            lambda: 1,
        )

    # It stops at the refusal rather than writing the destinations after it.
    assert ["narrators"] == applied


def test_no_product_import_precedes_the_import_root_selection() -> None:
    """The qualifier imports the product only after choosing the artifact root.

    An earlier `mechanical_librarian` import would bind the checkout's package,
    so an installed-artifact run would exercise the source tree instead.
    """
    script = (
        Path(__file__).parents[2] / "scripts" / "qualify_audiobookshelf_field_effect.py"
    )
    body = ast.parse(script.read_text(encoding="utf-8")).body
    selection = next(
        index
        for index, node in enumerate(body)
        if isinstance(node, ast.If) and "IMPORT_ROOT" in ast.unparse(node)
    )
    early = [
        ast.unparse(node)
        for node in body[:selection]
        if isinstance(node, ast.Import | ast.ImportFrom)
        and any(
            name.startswith("mechanical_librarian")
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
        )
    ]
    assert early == []


_RECEIPT_KEYS = {
    "adapter_canary",
    "audiobook",
    "authentication",
    "cover",
    "description_landings",
    "destinations",
    "image_digest",
    "installed_automatic_series",
    "installed_continuous",
    "installed_owner_link",
    "least_authority_refusal",
    "metadata_tags",
    "mixed_origins",
    "operator_cases",
    "outcomes",
    "real_automatic_adapter",
    "resolved_image_digest",
    "version",
}


def _staged_qualify(
    monkeypatch: pytest.MonkeyPatch, *, fail_at: str | None = None
) -> tuple[list[str], list[str], dict[str, Path]]:
    """Replace each qualify phase with a recorder; Docker and the service stay out."""
    calls: list[str] = []
    deleted: list[str] = []
    roots: dict[str, Path] = {}
    version = "2.37.2"
    digest = qualification.IMAGES[version].rsplit("@", 1)[1]

    def step(name: str, value: object) -> Callable[..., object]:
        def run(*args: object) -> object:
            calls.append(name)
            if name == fail_at:
                raise QualificationError("staged_failure")
            return value

        return run

    def files(root: Path, corpus: Path | None) -> object:
        calls.append("files")
        roots["root"] = root
        return SimpleNamespace(corpus_sources=())

    def service(run: object, image: str, files: object) -> object:
        calls.append("service")
        assert isinstance(run, qualification._QualificationRun)
        assert image == qualification.IMAGES[version]
        run.base, run.root_token = "http://canary", "root-token"
        run.key_ids.extend(("writer", "read-only"))
        if fail_at == "service":
            raise QualificationError("staged_failure")
        return SimpleNamespace()

    repository = SimpleNamespace(close=lambda: calls.append("close"))
    rig = SimpleNamespace(repository=repository, outcomes=[{"state": "verified"}])
    installed = {
        "destinations": ["title", "cover"],
        "full_chain_origins": {"title": "automatic_policy", "cover": "human"},
        "outcomes": [{"state": "verified"}],
    }
    monkeypatch.setattr(qualification, "_canary_files", files)
    monkeypatch.setattr(qualification, "_start_canary_service", service)
    monkeypatch.setattr(
        qualification, "_scanned_canary_items", step("scanned", SimpleNamespace())
    )
    monkeypatch.setattr(
        qualification, "_least_authority_phase", step("authority", None)
    )
    monkeypatch.setattr(qualification, "_adapter_canary", step("adapter", rig))
    monkeypatch.setattr(
        qualification,
        "_library_canaries",
        step("library", ([{"state": "verified"}], {"outcomes": []}, [])),
    )
    monkeypatch.setattr(
        qualification,
        "_installed_phases",
        step("installed", (installed, {"installed_composition": True})),
    )
    monkeypatch.setattr(
        qualification,
        "_owner_link_phase",
        step("owner_link", {"installed_composition": True}),
    )
    monkeypatch.setattr(
        qualification,
        "_run",
        lambda *command, timeout=120: SimpleNamespace(
            stdout="ghcr.io/advplyr/audiobookshelf@" + digest + "\n"
        ),
    )
    monkeypatch.setattr(
        qualification.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(qualification, "_owned", lambda name, owner: False)
    monkeypatch.setattr(
        qualification,
        "_delete_key",
        lambda base, token, key_id: deleted.append(f"{base} {token} {key_id}"),
    )
    return calls, deleted, roots


def test_qualify_runs_its_phases_in_order_and_shapes_the_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, deleted, roots = _staged_qualify(monkeypatch)

    receipt = qualification.qualify("2.37.2")

    assert calls == [
        "files",
        "service",
        "scanned",
        "authority",
        "adapter",
        "library",
        "installed",
        "owner_link",
        "close",
    ]
    assert set(receipt) == _RECEIPT_KEYS
    assert receipt["installed_owner_link"] == {"installed_composition": True}
    assert receipt["version"] == "2.37.2"
    assert receipt["image_digest"] == receipt["resolved_image_digest"]
    assert receipt["mixed_origins"] is True and receipt["cover"] is True
    assert receipt["operator_cases"] == 0
    assert deleted == [
        "http://canary root-token writer",
        "http://canary root-token read-only",
    ]
    assert not roots["root"].exists()


@pytest.mark.parametrize("fail_at", ["service", "adapter", "installed", "owner_link"])
def test_a_failed_phase_still_deletes_its_keys_and_files(
    monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    calls, deleted, roots = _staged_qualify(monkeypatch, fail_at=fail_at)

    with pytest.raises(QualificationError, match="staged_failure"):
        qualification.qualify("2.37.2")

    assert calls[-1] == fail_at and "close" not in calls
    assert len(deleted) == 2
    assert not roots["root"].exists()


def test_an_unverified_adapter_write_fails_the_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _staged_qualify(monkeypatch)
    rig = SimpleNamespace(
        repository=SimpleNamespace(close=lambda: None),
        outcomes=[{"state": "stale"}],
    )
    monkeypatch.setattr(qualification, "_adapter_canary", lambda *args: rig)

    with pytest.raises(QualificationError, match="guarded_write_not_verified"):
        qualification.qualify("2.37.2")


def _owner_link_evidence() -> dict[str, object]:
    """Every link-phase observation as a passing installed run records it."""
    asin = qualification.OWNER_LINK_ASIN
    return {
        "installed_composition": True,
        "ebook": {
            "before": "not_matched",
            "before_link": None,
            "card_choosable": True,
            "card_scopes": ["work"],
            "action": "link",
            "replay_same_row": True,
            "scan_requested": True,
            "trigger_book_linked": True,
            "after": "needs_attention",
            "link_state": "linked",
            "link_lanes": ["Open Library"],
            "anchored_lanes": ["Hardcover"],
            "offered": ["authors", "cover", "description", "genres", "title"],
            "automatic_fields": [],
            "automatic_decisions": [["authors", "keep_current"]],
            "cover_selections": 0,
            "unqualified_reason": ["cover"],
            "chosen_destination": "description",
            "effects": [["description", "human", "verified"]],
            "readback_matches": True,
            "chosen_digest": "a" * 64,
            "readback_digest": "a" * 64,
        },
        "recording": {
            "before": "not_matched",
            "action": "link",
            "replay_same_row": True,
            "link_state": "linked",
            "audible_lookup_asins": [asin],
            "audnexus_asins": [asin],
            "audiosilo_asins": [asin],
            "confirmed_by": ["AudioSilo"],
        },
        "out_of_band": {
            "choosable": True,
            "scopes": ["work"],
            "withheld": [{"scope": "recording", "reason": "runtime"}],
        },
        "withdrawal": {
            "action": "withdraw",
            "link": None,
            "link_lanes": [],
            "anchored_lanes": [],
            "history": ["link_withdrawn", "linked"],
        },
        "unexpected_requests": 0,
    }


def test_owner_link_evidence_passes_only_when_every_row_21_claim_holds() -> None:
    qualification._require_installed_owner_link_evidence(_owner_link_evidence())


@pytest.mark.parametrize(
    ("section", "key", "value", "failed"),
    [
        # The defect the first installed run found: anchor-admitted genres
        # decided and written automatically (contract line 53, S26).
        (
            "ebook",
            "automatic_decisions",
            [["authors", "keep_current"], ["genres", "candidate"]],
            "no_automatic_decision",
        ),
        (
            "ebook",
            "effects",
            [
                ["description", "human", "verified"],
                ["genres", "automatic_policy", "verified"],
            ],
            "no_automatic_effect",
        ),
        ("ebook", "automatic_fields", ["genres"], "no_automatic_decision"),
        ("ebook", "cover_selections", 1, "no_cover_selection"),
        ("ebook", "before", "no_attention_needed", "ebook_was_not_matched"),
        ("ebook", "after", "not_matched", "ebook_left_not_matched"),
        ("ebook", "trigger_book_linked", False, "book_linked_trigger"),
        ("ebook", "offered", ["cover", "description"], "offered_for_review"),
        ("ebook", "readback_digest", "b" * 64, "exact_readback"),
        ("ebook", "anchored_lanes", [], "anchor_steers_other_source"),
        ("recording", "audiosilo_asins", [], "asin_reached_audiosilo"),
        ("recording", "audnexus_asins", ["B000OTHER1"], "asin_reached_audnexus"),
        ("recording", "link_state", "contradicted", "recording_linked"),
        ("out_of_band", "scopes", ["work", "recording"], "out_of_band_book_only"),
        ("withdrawal", "link_lanes", ["Open Library"], "link_evidence_gone"),
        ("withdrawal", "history", ["linked"], "history_link_and_withdrawal"),
    ],
)
def test_owner_link_evidence_names_each_unproved_claim(
    section: str, key: str, value: object, failed: str
) -> None:
    evidence = _owner_link_evidence()
    cast_section = evidence[section]
    assert isinstance(cast_section, dict)
    cast_section[key] = value

    with pytest.raises(QualificationError) as raised:
        qualification._require_installed_owner_link_evidence(evidence)

    assert failed in json.loads(str(raised.value).split(":", 1)[1])


def test_link_catalogue_answers_the_owner_search_and_records_each_asin() -> None:
    """Real-consumer dry proof: the real adapters read the synthetic sources."""
    from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
        AudibleCatalogMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.open_library_metadata import (
        OpenLibraryMetadataProvider,
    )
    from mechanical_librarian.domain.owner_link import OwnerSearchQuery

    catalogue = qualification._LinkCatalogue()
    client = qualification._owner_link_catalogue(catalogue)
    open_library = OpenLibraryMetadataProvider("catalogue", client(), 262_144)
    audible = AudibleCatalogMetadataProvider(
        "audible", client(), 262_144, marketplaces=("us",)
    )
    title = OwnerSearchQuery("title_authors", title="scan", authors=("A",))
    audio = OwnerSearchQuery(
        "title_authors", title="track01", authors=("A",), marketplace="us"
    )

    # Preparation's own discovery finds nothing: the works start Not matched.
    assert open_library.owner_search(title, deadline=1e12).records == ()
    catalogue.owner_search = True
    (record,) = open_library.owner_search(title, deadline=1e12).records
    products = audible.owner_search(audio, deadline=1e12).records
    catalogue.owner_search = False

    assert record.identity == qualification.OWNER_LINK_WORK
    assert {value.identity for value in products} == {
        qualification.OWNER_LINK_ASIN,
        qualification.OWNER_LINK_OUT_OF_BAND_ASIN,
    }
    for host, path in (
        ("api.audnex.us", "/books/" + qualification.OWNER_LINK_ASIN),
        ("meta.audiosilo.app", "/api/v1/lookup"),
    ):
        client().get(
            f"https://{host}{path}", params={"asin": qualification.OWNER_LINK_ASIN}
        )
    assert catalogue.asins("audnexus") == [qualification.OWNER_LINK_ASIN]
    assert catalogue.asins("audiosilo") == [qualification.OWNER_LINK_ASIN]
    assert catalogue.asins("unexpected") == []
