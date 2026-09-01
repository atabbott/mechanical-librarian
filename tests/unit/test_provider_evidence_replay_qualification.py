from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import pytest
from scripts import qualify_provider_evidence_replay as replay

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    REPORT_SCHEMA,
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    ProviderQueryKind,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.metadata_report_schemas import synthetic_report

_HEAD = "1" * 40


def test_recorded_identifier_hit_replays_without_discovery_spend() -> None:
    claim = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN: 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    plan = MetadataDiscoveryPlan(
        (claim,),
        (
            MetadataDiscoveryLane(
                "lane_" + "1" * 64,
                ProviderQueryKind.WORK,
                (claim.reference,),
                title="Example Book",
                authors=("Ada Example",),
            ),
            MetadataDiscoveryLane(
                "lane_" + "2" * 64,
                ProviderQueryKind.ISBN,
                (claim.reference,),
                matched_family="9780306406157",
                canonical_isbns=("9780306406157",),
            ),
        ),
    )
    payload = {
        "totalItems": 1,
        "items": [
            {
                "id": "synthetic-volume",
                "volumeInfo": {
                    "title": "Example Book",
                    "authors": ["Ada Example"],
                    "industryIdentifiers": [
                        {"type": "ISBN_13", "identifier": "9780306406157"}
                    ],
                },
            }
        ],
    }
    exchanges: list[dict[str, object]] = []
    recorded = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=replay.RecordingTransport(
                exchanges,
                "google_books",
                httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,
                        request=request,
                    )
                ),
            )
        ),
        65_536,
        wall_clock=lambda: 1_700_000_000.0,
    ).observe(plan)
    transport = replay.ReplayTransport(exchanges, "google_books")
    replayed = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=transport),
        65_536,
        wall_clock=lambda: 1_700_000_000.0,
    ).observe(plan)

    assert recorded.to_data() == replayed.to_data()
    assert [ProviderQueryKind.ISBN] == [lane.kind for lane in replayed.lanes]
    assert 1 == replayed.request_accounting.logical_requests
    assert transport.cursor == len(exchanges) == 1
    assert not transport.mismatch


def _configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        connections=(
            TargetConnectionConfiguration(
                "primary", "https://target.example.invalid", None
            ),
        ),
        libraries=(
            LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "book",
                None,
                ("open-library",),
            ),
        ),
        mounts=(),
        providers=(
            ProviderConnectionConfiguration(
                "open-library",
                True,
                False,
                None,
                "open_library",
                "owner@example.invalid",
            ),
        ),
    )


def _seed() -> MetadataReport:
    retained = synthetic_report()
    claim = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN: 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    return MetadataReport.create(
        target=retained.target,
        file=retained.file,
        local_claims=retained.local_claims + (claim,),
        local_reasons=retained.local_reasons,
        providers=(),
    )


def _bundle() -> dict[str, object]:
    configuration = _configuration()
    provider = configuration.providers[0]
    seed = _seed()
    plan = metadata_discovery_plan(replay._target_snapshot(seed), seed.local_claims)
    exchanges: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
            request=request,
        )

    transport = replay.RecordingTransport(
        exchanges,
        "open_library",
        httpx.MockTransport(handler),
    )
    instance = replay.build_provider(
        provider,
        configuration,
        transport,
        live=True,
        capture_epoch=1_700_000_000.0,
        pacers=replay.pacers(live=False),
    )
    observation = replay.observe_provider(
        provider.name,
        provider.adapter,
        instance,
        plan,
    )
    report = MetadataReport.create(
        target=seed.target,
        file=seed.file,
        local_claims=seed.local_claims,
        local_reasons=seed.local_reasons,
        providers=(observation,),
    )
    proposal = evaluate_report(report)
    value: dict[str, object] = {
        "capture_head": _HEAD,
        "configuration_schema": configuration.schema_version,
        "created_at": "2026-08-30T20:00:00+01:00",
        "report_schema": REPORT_SCHEMA,
        "rows": [
            {
                "category": "isbn",
                "expected_proposal_sha256": hashlib.sha256(
                    proposal.to_json().encode()
                ).hexdigest(),
                "expected_report_sha256": hashlib.sha256(
                    report.to_json().encode()
                ).hexdigest(),
                "providers": [
                    {
                        "adapter": provider.adapter,
                        "capture_epoch": 1_700_000_000.0,
                        "exchanges": exchanges,
                        "name": provider.name,
                    }
                ],
                "seed": json.loads(seed.to_json()),
                "source_report_sha256": "2" * 64,
            }
        ],
        "schema": replay._BUNDLE_SCHEMA,
    }
    replay.validate_bundle(value)
    return value


def test_synthetic_capture_boundary_replays_current_derivation_exactly() -> None:
    bundle = _bundle()
    raw = replay.canonical(bundle)
    reports, invalidated, provenance = replay.replay(bundle, _configuration())

    assert b"owner@example.invalid" not in raw
    assert b"<contact>" in raw
    assert [] == invalidated
    assert [
        {
            "proposal_sha256": bundle["rows"][0]["expected_proposal_sha256"],
            "report_sha256": bundle["rows"][0]["expected_report_sha256"],
            "row": 0,
            "same_as_capture": True,
        }
    ] == provenance
    assert reports[0].providers[0].provider == "open-library"
    assert reports[0].providers[0].authority == "open_library"


def test_request_drift_invalidates_only_the_affected_provider() -> None:
    bundle = copy.deepcopy(_bundle())
    bundle["rows"][0]["providers"][0]["exchanges"][0]["request"]["path"] += "/changed"
    replay.validate_bundle(bundle)

    reports, invalidated, provenance = replay.replay(bundle, _configuration())

    assert [
        {
            "provider": "open-library",
            "reason": "request_contract_changed",
            "row": 0,
        }
    ] == invalidated
    assert () == reports[0].providers
    assert provenance[0]["same_as_capture"] is False


def test_newly_enabled_provider_is_explicitly_invalidated() -> None:
    bundle = _bundle()
    base = _configuration()
    google = ProviderConnectionConfiguration(
        "google-books",
        True,
        False,
        None,
        "google_books",
        None,
    )
    configuration = replace(
        base,
        libraries=(
            replace(
                base.libraries[0],
                provider_connections=("google-books", "open-library"),
            ),
        ),
        providers=tuple(sorted((*base.providers, google), key=lambda item: item.name)),
    )

    reports, invalidated, provenance = replay.replay(bundle, configuration)

    assert [
        {
            "provider": "google-books",
            "reason": "provider_evidence_missing",
            "row": 0,
        }
    ] == invalidated
    assert ("open-library",) == tuple(
        observation.provider for observation in reports[0].providers
    )
    assert provenance[0]["same_as_capture"] is True


def test_replay_has_no_live_transport_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle()

    def forbidden_transport(*_args, **_kwargs):
        raise AssertionError("live transport constructed")

    monkeypatch.setattr(httpx, "HTTPTransport", forbidden_transport)

    reports, invalidated, _provenance = replay.replay(bundle, _configuration())

    assert 1 == len(reports)
    assert [] == invalidated


def test_replay_preserves_credential_free_google_books_requests() -> None:
    base = _configuration()
    google = ProviderConnectionConfiguration(
        "google-books",
        True,
        False,
        None,
        "google_books",
        None,
    )
    configuration = replace(
        base,
        libraries=(
            replace(
                base.libraries[0],
                provider_connections=("google-books",),
            ),
        ),
        providers=(google,),
    )
    seed = _seed()
    plan = metadata_discovery_plan(replay._target_snapshot(seed), seed.local_claims)
    exchanges: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"totalItems": 0, "items": []},
            request=request,
        )

    recorder = replay.RecordingTransport(
        exchanges,
        "google_books",
        httpx.MockTransport(handler),
    )
    live = replay.build_provider(
        google,
        configuration,
        recorder,
        live=True,
        capture_epoch=1_700_000_000.0,
        pacers=replay.pacers(live=False),
    )
    replay.observe_provider(google.name, google.adapter, live, plan)
    transport = replay.ReplayTransport(exchanges, "google_books")
    offline = replay.build_provider(
        google,
        configuration,
        transport,
        live=False,
        capture_epoch=1_700_000_000.0,
        pacers=replay.pacers(live=False),
    )

    replay.observe_provider(google.name, google.adapter, offline, plan)

    assert transport.mismatch is False
    assert transport.cursor == len(exchanges)
    assert not any(
        pair[0] == "key"
        for exchange in exchanges
        for pair in exchange["request"]["query"]
    )


def test_replay_preserves_inventaire_repeated_query_parameters() -> None:
    base = _configuration()
    inventaire = ProviderConnectionConfiguration(
        "inventaire", True, True, None, "inventaire", None
    )
    configuration = replace(
        base,
        libraries=(replace(base.libraries[0], provider_connections=("inventaire",)),),
        providers=(inventaire,),
    )
    seed = _seed()
    plan = metadata_discovery_plan(replay._target_snapshot(seed), seed.local_claims)
    exchanges: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = (
            {"results": []}
            if request.url.path == "/api/search"
            else {
                "entities": {
                    "inv:edition-one": {
                        "uri": "inv:edition-one",
                        "claims": {"wdt:P212": ["9780306406157"]},
                        "labels": {"fromclaims": "Example Book"},
                    }
                },
                "redirects": {"isbn:9780306406157": "inv:edition-one"},
            }
        )
        return httpx.Response(200, json=payload, request=request)

    recorder = replay.RecordingTransport(
        exchanges, "inventaire", httpx.MockTransport(handler)
    )
    live = replay.build_provider(
        inventaire,
        configuration,
        recorder,
        live=True,
        capture_epoch=1_700_000_000.0,
        pacers=replay.pacers(live=False),
    )
    replay.observe_provider(inventaire.name, inventaire.adapter, live, plan)
    transport = replay.ReplayTransport(exchanges, "inventaire")
    offline = replay.build_provider(
        inventaire,
        configuration,
        transport,
        live=False,
        capture_epoch=1_700_000_000.0,
        pacers=replay.pacers(live=False),
    )

    replay.observe_provider(inventaire.name, inventaire.adapter, offline, plan)

    assert transport.mismatch is False
    assert transport.cursor == len(exchanges)
    entity = next(
        exchange
        for exchange in exchanges
        if exchange["request"]["path"] == "/api/entities/by-uris"
    )
    query = entity["request"]["query"]
    assert [value for key, value in query if key == "attributes"] == [
        "claims",
        "labels",
        "image",
    ]


def test_capture_refuses_a_contact_echo_before_bundle_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = replay.RecordingTransport

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": [], "echo": "owner@example.invalid"},
            request=request,
        )

    monkeypatch.setattr(
        replay,
        "RecordingTransport",
        lambda exchanges, adapter: original(
            exchanges, adapter, httpx.MockTransport(handler)
        ),
    )

    with pytest.raises(replay.ReplayFailure, match="private_identity_persisted"):
        replay.capture(_configuration(), [_seed()], _HEAD)


def test_capture_decodes_compressed_content_before_private_identity_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = replay.RecordingTransport

    def handler(request: httpx.Request) -> httpx.Response:
        payload = gzip.compress(b'{"docs":[],"echo":"owner@example.invalid"}')
        return httpx.Response(
            200,
            headers={
                "content-encoding": "gzip",
                "content-type": "application/json",
            },
            content=payload,
            request=request,
        )

    monkeypatch.setattr(
        replay,
        "RecordingTransport",
        lambda exchanges, adapter: original(
            exchanges, adapter, httpx.MockTransport(handler)
        ),
    )

    with pytest.raises(replay.ReplayFailure, match="private_identity_persisted"):
        replay.capture(_configuration(), [_seed()], _HEAD)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda request: request.__setitem__("host", "elsewhere.invalid"), "host"),
        (
            lambda request: request["headers"].append(
                ["authorization", "private-token"]
            ),
            "credential",
        ),
        (
            lambda request: request["headers"].append(
                ["user-agent", "owner@example.invalid"]
            ),
            "contact",
        ),
    ],
)
def test_bundle_rejects_unconfined_or_private_request_material(
    mutation, reason: str
) -> None:
    bundle = copy.deepcopy(_bundle())
    request = bundle["rows"][0]["providers"][0]["exchanges"][0]["request"]
    mutation(request)
    if reason != "host":
        request["headers"].sort()

    with pytest.raises(replay.ReplayFailure, match="bundle_request_invalid"):
        replay.validate_bundle(bundle)


def test_request_document_replaces_closed_secret_and_contact_surfaces() -> None:
    request = httpx.Request(
        "POST",
        "https://www.googleapis.com/books/v1/volumes?key=private&q=isbn%3A1",
        headers={
            "authorization": "Bearer private",
            "content-type": "application/json",
            "user-agent": "qualifier owner@example.invalid",
        },
        content=b"{}",
    )

    document = replay.request_document(request)
    query = cast("list[list[str]]", document["query"])
    headers = cast("list[list[str]]", document["headers"])

    assert ["key", "<credential>"] in query
    assert ["authorization", "<credential>"] in headers
    assert not any("owner@example.invalid" in value for _, value in headers)


def test_canonical_digest_bound_private_bundle_read(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    bundle = replay.canonical(_bundle())
    bundle_path = tmp_path / "bundle.json"
    digest_path = tmp_path / "bundle.json.sha256"
    bundle_path.write_bytes(bundle)
    digest_path.write_text(hashlib.sha256(bundle).hexdigest() + "\n")
    bundle_path.chmod(0o600)
    digest_path.chmod(0o600)

    value, identity = replay.read_bundle(bundle_path, digest_path)

    assert replay._BUNDLE_SCHEMA == value["schema"]
    assert hashlib.sha256(bundle).hexdigest() == identity

    bundle_path.write_bytes(bundle + b"\n")
    with pytest.raises(replay.ReplayFailure, match="bundle_digest_invalid"):
        replay.read_bundle(bundle_path, digest_path)


def test_selection_reads_only_named_reports_from_an_exact_current_database(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "source.sqlite3"
    source = _seed()
    repository = open_repository(database)
    try:
        repository.publish(source, lambda: True)
    finally:
        repository.close()
    selection_path = tmp_path / "selection.json"
    selection = replay.canonical(
        {"report_ids": [source.report_id], "schema": replay._SELECTION_SCHEMA}
    )
    selection_path.write_bytes(selection)
    selection_path.chmod(0o600)

    report_ids = replay.load_selection(selection_path)
    reports = replay.load_reports(database, report_ids)

    assert (source.report_id,) == report_ids
    assert [source.to_json()] == [report.to_json() for report in reports]


def test_selected_report_envelope_is_validated_before_capture(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "source.sqlite3"
    source = _seed()
    replacement = MetadataReport.create(
        target=source.target,
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=("replacement",),
        providers=(),
    )
    repository = open_repository(database)
    try:
        repository.publish(source, lambda: True)
    finally:
        repository.close()
    document = replacement.to_json()
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP TRIGGER metadata_reports_no_update")
        connection.execute(
            "UPDATE metadata_reports SET document_digest=?, document=? WHERE report_id=?",
            (hashlib.sha256(document.encode()).hexdigest(), document, source.report_id),
        )
        connection.execute(
            "CREATE TRIGGER metadata_reports_no_update BEFORE UPDATE ON "
            "metadata_reports BEGIN SELECT RAISE(ABORT, "
            "'metadata reports are append-only'); END"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(replay.ReplayFailure, match="selected_report_invalid"):
        replay.load_reports(database, (source.report_id,))


def test_cover_bearing_retained_report_builds_a_consistent_discovery_snapshot() -> None:
    seed = _seed()
    fields = (
        *seed.target.fields[:-1],
        TargetField(
            TargetDestination.COVER,
            TargetFieldState.VALUE,
            "covers/retained.jpg",
        ),
    )
    cover_bearing = MetadataReport.create(
        target=replace(seed.target, fields=fields),
        file=seed.file,
        local_claims=seed.local_claims,
        local_reasons=seed.local_reasons,
        providers=(),
    )

    snapshot = replay._target_snapshot(cover_bearing)
    plan = metadata_discovery_plan(snapshot, cover_bearing.local_claims)

    assert snapshot.cover is not None
    assert plan.lanes


def test_endpoint_confinement_precedes_upstream_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request)

    transport = replay.RecordingTransport(
        [], "open_library", httpx.MockTransport(handler)
    )

    with pytest.raises(replay.ReplayFailure, match="provider_endpoint_invalid"):
        transport.handle_request(httpx.Request("GET", "https://elsewhere.invalid"))
    assert 0 == calls


def test_configuration_rejects_a_replaceable_parent_before_parsing(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o700)
    configuration = unsafe / "config.json"
    configuration.write_bytes(b"{}")
    configuration.chmod(0o600)
    unsafe.chmod(0o777)

    with pytest.raises(replay.ReplayFailure, match="private_authority_unsafe"):
        replay.load_private_configuration(configuration)


def test_exact_source_authority_rejects_untracked_code(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Maintainer"],
        cwd=repository,
        check=True,
    )
    tracked = repository / "tracked.py"
    tracked.write_text("value = 1\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "test"], cwd=repository, check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    replay.require_environment(repository, head)

    (repository / "untracked.py").write_text("value = 2\n")

    with pytest.raises(replay.ReplayFailure, match="source_repository_invalid"):
        replay.require_environment(repository, head)


def test_private_authority_refuses_symlinks_permissions_and_overwrite(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    source = tmp_path / "source.json"
    source.write_bytes(b"{}")
    source.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(source)
    with pytest.raises(replay.ReplayFailure, match="private_authority_unsafe"):
        replay.read_private(link, 16)

    source.chmod(0o640)
    with pytest.raises(replay.ReplayFailure, match="private_authority_unsafe"):
        replay.read_private(source, 16)

    output = tmp_path / "output.json"
    output.write_bytes(b"incumbent")
    output.chmod(0o600)
    with pytest.raises(replay.ReplayFailure, match="private_output_unavailable"):
        replay.private_write(output, b"replacement")
    assert b"incumbent" == output.read_bytes()


def test_incomplete_response_requires_an_explicit_stream_failure() -> None:
    bundle = copy.deepcopy(_bundle())
    response = bundle["rows"][0]["providers"][0]["exchanges"][0]["response"]
    response["complete"] = False

    with pytest.raises(replay.ReplayFailure, match="bundle_response_invalid"):
        replay.validate_bundle(bundle)

    response["stream_failure"] = "ReadError"
    replay.validate_bundle(bundle)


def test_replay_publishes_only_fresh_current_derivation(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    reports, invalidated, _provenance = replay.replay(_bundle(), _configuration())
    database = tmp_path / "current.sqlite3"

    counts = replay.publish_database(database, reports)

    assert [] == invalidated
    assert {"admitted": 1, "cases": 1, "proposals": 1, "reports": 1} == counts
    assert 0o600 == database.stat().st_mode & 0o777
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        assert ("ok",) == connection.execute("PRAGMA integrity_check").fetchone()
        assert (0,) == connection.execute(
            "SELECT count(*) FROM shadow_decision_outcomes"
        ).fetchone()
    finally:
        connection.close()


def test_cli_failure_is_value_free(capsys: pytest.CaptureFixture[str]) -> None:
    private_path = "/private/owner/library/report.json"

    assert 1 == replay.main(
        [
            "replay",
            "--repository",
            private_path,
            "--expected-head",
            _HEAD,
            "--config",
            private_path,
            "--output-directory",
            private_path,
            "--bundle",
            private_path,
            "--bundle-digest",
            private_path,
        ]
    )
    captured = capsys.readouterr()
    assert "" == captured.out
    assert "Provider-evidence qualification failed safely.\n" == captured.err
    assert private_path not in captured.err


def test_cli_argument_errors_are_value_free_but_help_remains_available(
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_path = "/private/owner/library/unexpected.json"

    assert 1 == replay.main(["replay", private_path])
    failed = capsys.readouterr()
    assert "" == failed.out
    assert "Provider-evidence qualification failed safely.\n" == failed.err
    assert private_path not in failed.err

    with pytest.raises(SystemExit) as help_exit:
        replay.main(["--help"])
    assert help_exit.value.code == 0
    helped = capsys.readouterr()
    assert "Capture or replay" in helped.out
    assert "" == helped.err


def test_noncanonical_and_oversized_documents_fail_before_authority() -> None:
    with pytest.raises(replay.ReplayFailure, match="private_document_noncanonical"):
        replay.parse_canonical(b'{"schema": "value"}')
    with pytest.raises(replay.ReplayFailure, match="bundle_response_invalid"):
        replay._base64(
            base64.b64encode(b"x" * 17).decode(),
            16,
            "bundle_response_invalid",
        )
