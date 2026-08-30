from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import cast

import httpx
import pytest
from scripts import qualify_provider_evidence_replay as replay

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import metadata_discovery_plan
from mechanical_librarian.domain.metadata_report import (
    REPORT_SCHEMA,
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.metadata_report_schemas import synthetic_report

_HEAD = "1" * 40


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
    observation = instance.observe(plan)
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


def test_noncanonical_and_oversized_documents_fail_before_authority() -> None:
    with pytest.raises(replay.ReplayFailure, match="private_document_noncanonical"):
        replay.parse_canonical(b'{"schema": "value"}')
    with pytest.raises(replay.ReplayFailure, match="bundle_response_invalid"):
        replay._base64(
            base64.b64encode(b"x" * 17).decode(),
            16,
            "bundle_response_invalid",
        )
