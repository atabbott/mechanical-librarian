from __future__ import annotations

import gzip
import json
from typing import Any

import httpx
import pytest
from scripts import measure_reviewer_cost as measure

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.contained_reviewer_contract import (
    admit_cover_review_request,
)
from mechanical_librarian.application.cover_observation import (
    OUTPUT_SCHEMA_VERSION,
    TAXONOMY,
)
from mechanical_librarian.application.cover_packets import prepare_cover_packet
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.audiobook_synopsis import recording_synopsis_report
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.cover_producers import _preview, _source
from tests.support.description_advice import _contained_profile


@pytest.fixture
def frozen(tmp_path):
    database = tmp_path / "reports.sqlite"
    artifacts = tmp_path / "covers"
    store = CoverArtifactStore(artifacts)
    repository = open_repository(database)
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_audiobook_identity,
    )

    audio_report = recording_synopsis_report()
    assert admitted_audiobook_identity(audio_report)
    reports = []
    try:
        for index, base in enumerate(
            (
                _automatic_no_change_inputs("description_preserved")[0],
                audio_report,
            ),
            1,
        ):
            image = _preview(index)
            source = _source(index, image, incumbent=True)
            assert source.artifact is not None
            store.stage(source.artifact, f"original-{index}".encode(), image)
            report = MetadataReport.create(
                target=base.target,
                file=base.file,
                local_claims=base.local_claims,
                local_reasons=base.local_reasons,
                providers=base.providers,
                audio=base.audio,
                cover_sources=(source,),
            )
            repository.publish(report, lambda: True)
            reports.append(report)
    finally:
        repository.close()
    cohort = measure.Cohort(
        schema_version="reviewer-cost-cohort-v1",
        report_ids=tuple(report.report_id for report in reports),
    )
    before = database.read_bytes()
    inputs = measure.inventory(database, artifacts, cohort)
    assert database.read_bytes() == before
    assert len(inputs) == 2
    assert all(value.cover_document and value.description_document for value in inputs)
    yield inputs, database, artifacts, cohort
    assert database.read_bytes() == before


def transport_factory(
    inputs, *, charge="0.01", invalid=False, interrupt=False, omit_request_fee=False
):
    exchanges = []

    def factory():
        def respond(request):
            exchanges.append(
                (request.method, request.url.path, measure.digest(request.content))
            )
            if request.url.path == "/api/v1/key":
                return httpx.Response(200, json={"data": {"is_free_tier": False}})
            if request.url.path == "/api/v1/models":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "example/model",
                                "architecture": {
                                    "input_modalities": ["text", "image"],
                                    "output_modalities": ["text"],
                                },
                                "supported_parameters": ["structured_outputs"],
                                "pricing": {
                                    "prompt": "0",
                                    "completion": "0",
                                    **({} if omit_request_fee else {"request": "0"}),
                                },
                            }
                        ]
                    },
                )
            assert (
                request.method == "POST"
                and request.url.path == "/api/v1/chat/completions"
            )
            if interrupt:
                raise KeyboardInterrupt
            body = json.loads(request.content)
            content: dict[str, Any]
            if body["response_format"]["json_schema"]["name"] == "description_rewrite":
                content = {
                    "description": "A synthetic voyage uncovers a city.",
                    "disposition": "ready",
                }
            else:
                packet = json.loads(
                    body["messages"][0]["content"][0]["text"].split(
                        "\nSealed packet:\n"
                    )[1]
                )
                content = {
                    "schema_version": OUTPUT_SCHEMA_VERSION,
                    "observations": [
                        {
                            "candidate_reference": row["reference"],
                            **{name: values[0][0] for name, values in TAXONOMY},
                        }
                        for row in packet["candidates"]
                    ],
                }
            # Hidden reasoning is deliberately present: capture must discard it.
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "id": "synthetic-generation",
                        "model": "example/model",
                        "usage": {}
                        if charge is None
                        else {"cost": float(charge), "prompt_tokens": 10},
                        "choices": [
                            {
                                "message": {
                                    "content": "invalid"
                                    if invalid
                                    else json.dumps(content),
                                    "reasoning": "DO_NOT_RETAIN",
                                }
                            }
                        ],
                    }
                ).encode(),
            )

        return httpx.MockTransport(respond)

    return factory, exchanges


def manifest(inputs):
    return {
        "source_head": "a" * 40,
        "cohort_sha256": "b" * 64,
        "inventory_sha256": measure.digest(
            measure.encoded([value.binding() for value in inputs])
        ),
    }


@pytest.mark.parametrize("omit_request_fee", [False, True])
def test_composed_real_reports_packets_dispatch_capture_and_read_only(
    frozen, tmp_path, omit_request_fee
):
    inputs, database, artifacts, cohort = frozen
    for value in inputs:

        def read_preview(
            artifact: str, variant: str, value: measure.FrozenInput = value
        ) -> bytes:
            return CoverArtifactStore(artifacts, create=False).read_preview_digest(
                next(
                    source.artifact.preview_digest
                    for source in MetadataReport.from_json(
                        value.report_document
                    ).cover_sources
                    if source.artifact is not None
                    and source.artifact.artifact_id == artifact
                )
            )

        packet = prepare_cover_packet(value.report_document, read_preview)
        assert value.cover_document == packet.document
        assert value.images == tuple(
            (image.reference, image.digest, image.payload) for image in packet.images
        )
    selected = measure.selection(
        inputs, ("example/model",), ("example/model",), 50, None
    )
    assert [(row[2], row[3]) for row in selected] == [
        ("cover", 0),
        ("cover", 1),
        ("description", 0),
        ("description", 1),
    ]
    factory, exchanges = transport_factory(inputs, omit_request_fee=omit_request_fee)
    directory = tmp_path / "capture"
    result = measure.execute(
        directory,
        inputs,
        selected,
        manifest(inputs),
        "synthetic-secret",
        "1",
        "1",
        transport=factory,
    )
    assert result["status"] == "complete"
    assert [
        (row["task"], row["requests_completed"], row["per_1000_usd"])
        for row in result["public_evidence"]["models"]
    ] == [("cover", 2, "10.00"), ("description", 2, "10.00")]
    assert [row[0] for row in exchanges] == ["GET", "GET", "POST"] * 4
    assert all(
        (directory / f"attempt-{index}" / "final-response.txt").is_file()
        for index in range(4)
    )
    retained = b"".join(
        path.read_bytes() for path in directory.rglob("*") if path.is_file()
    )
    assert b"synthetic-secret" not in retained and b"DO_NOT_RETAIN" not in retained
    assert all(path.stat().st_mode & 0o077 == 0 for path in directory.rglob("*"))
    with pytest.raises(FileExistsError):
        measure.execute(
            directory,
            inputs,
            selected,
            manifest(inputs),
            "secret",
            "1",
            "1",
            transport=factory,
        )
    assert len(exchanges) == 12


@pytest.mark.parametrize(
    "charge,threshold,expected,attempts",
    [
        ("0.2", "0.1", "incomplete", 1),
        (None, "1", "incomplete", 1),
        ("0", "1", "complete", 2),
    ],
)
@pytest.mark.parametrize("description", [False, True])
def test_monitored_threshold_overshoot_unknown_and_zero(
    frozen, tmp_path, charge, threshold, expected, attempts, description
):
    inputs, *_ = frozen
    factory, exchanges = transport_factory(
        inputs, charge=charge, omit_request_fee=description
    )
    result = measure.execute(
        tmp_path / "run",
        inputs,
        measure.selection(
            inputs,
            () if description else ("example/model",),
            ("example/model",) if description else (),
            50,
            None,
        ),
        manifest(inputs),
        "secret",
        "0.1" if threshold == "0.1" else "1",
        threshold,
        transport=factory,
    )
    assert result["status"] == expected and result["requests_attempted"] == attempts
    assert len([row for row in exchanges if row[0] == "POST"]) == attempts
    if expected == "incomplete":
        assert result["public_evidence"] is None


@pytest.mark.parametrize("description", [False, True])
@pytest.mark.parametrize("stop_on_failure", [False, True])
def test_charged_parser_failure_is_in_attempt_denominator(
    frozen, tmp_path, description, stop_on_failure
):
    inputs, *_ = frozen
    factory, exchanges = transport_factory(
        inputs, invalid=True, omit_request_fee=description
    )
    result = measure.execute(
        tmp_path / "run",
        inputs,
        measure.selection(
            inputs,
            () if description else ("example/model",),
            ("example/model",) if description else (),
            50,
            None,
        ),
        manifest(inputs),
        "secret",
        "1",
        "1",
        transport=factory,
        stop_on_failure=stop_on_failure,
    )
    saved_manifest = json.loads((tmp_path / "run/manifest.json").read_bytes())
    if stop_on_failure:
        assert saved_manifest["stop_on_failure"] is True
        assert result["status"] == "incomplete"
        assert result["requests_attempted"] == 1
        assert result["known_cost_usd"] == "0.01"
        assert result["public_evidence"] is None
        assert len([row for row in exchanges if row[0] == "POST"]) == 1
        terminal = json.loads((tmp_path / "run/attempt-0/terminal.json").read_bytes())
        assert terminal["failure"] == "dispatch_or_parse_failed"
        assert terminal["charge_usd"] == "0.01"
        assert not (tmp_path / "run/attempt-1").exists()
        assert (
            tmp_path / "run/attempt-0/final-response.txt"
        ).read_bytes() == b"invalid"
        return
    assert "stop_on_failure" not in saved_manifest
    row = result["public_evidence"]["models"][0]
    assert row["requests_completed"] == 0 and row["requests_failed"] == 2
    assert row["per_1000_usd"] == "10.00"
    assert (tmp_path / "run/attempt-0/final-response.txt").read_bytes() == b"invalid"


def test_interruption_retains_durable_start_without_resume_or_public_rows(
    frozen, tmp_path
):
    inputs, *_ = frozen
    factory, exchanges = transport_factory(inputs, interrupt=True)
    with pytest.raises(KeyboardInterrupt):
        measure.execute(
            tmp_path / "run",
            inputs,
            measure.selection(inputs, ("example/model",), (), 50, None),
            manifest(inputs),
            "secret",
            "1",
            "1",
            transport=factory,
        )
    assert (tmp_path / "run/attempt-0/start.json").is_file()
    assert not (tmp_path / "run/attempt-0/terminal.json").exists()
    assert not (tmp_path / "run/result.json").exists()
    assert len(exchanges) == 3


def test_changed_image_and_duplicate_cohort_refuse(frozen):
    inputs, database, artifacts, cohort = frozen
    with pytest.raises(ValueError):
        measure.Cohort(
            schema_version="reviewer-cost-cohort-v1",
            report_ids=(cohort.report_ids[0], cohort.report_ids[0]),
        )
    report = MetadataReport.from_json(inputs[0].report_document)
    artifact = report.cover_sources[0].artifact
    assert artifact is not None
    image = artifacts / artifact.preview_digest
    # Store layout is digest-only; tampering is a reachable owner-file failure.
    assert image.is_file()
    image.write_bytes(b"changed")
    with pytest.raises(MetadataReportError):
        measure.inventory(database, artifacts, cohort)


def test_default_inventory_has_zero_external_work(
    frozen, tmp_path, monkeypatch, capsys
):
    _, database, artifacts, cohort = frozen
    path = tmp_path / "cohort.json"
    path.write_text(cohort.model_dump_json())
    path.chmod(0o600)
    monkeypatch.setattr(
        "sys.argv",
        [
            "measure",
            "--database",
            str(database),
            "--artifacts",
            str(artifacts),
            "--cohort",
            str(path),
        ],
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("inventory attempted external work")

    monkeypatch.setattr(measure, "OpenRouterReviewer", forbidden)
    monkeypatch.setattr(measure, "run_cover_packet_reviewer", forbidden)
    assert measure.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["selected"] == [] and result["description_eligible"] == 2
    assert len(result["inventory_sha256"]) == 64


@pytest.mark.parametrize(
    "target,threshold", [("NaN", "1"), ("1", "0"), ("1", "2"), ("Infinity", "1")]
)
def test_invalid_budget_has_no_output_or_dispatch(frozen, tmp_path, target, threshold):
    inputs, *_ = frozen
    factory, exchanges = transport_factory(inputs)
    with pytest.raises(ValueError):
        measure.execute(
            tmp_path / "run",
            inputs,
            measure.selection(inputs, ("example/model",), (), 50, None),
            manifest(inputs),
            "secret",
            target,
            threshold,
            transport=factory,
        )
    assert exchanges == [] and not (tmp_path / "run").exists()


def test_first_n_description_selection_is_independent_and_never_duplicates(frozen):
    from dataclasses import replace

    inputs, *_ = frozen
    values = (
        replace(inputs[0], cover_document=None, images=()),
        replace(inputs[1], description_document=None),
    )
    assert measure.selection(
        values, ("example/model",), ("example/model",), 50, None
    ) == [
        ("openrouter", "example/model", "cover", 1),
        ("openrouter", "example/model", "description", 0),
    ]
    assert measure.selection(inputs, (), ("example/model",), 1, None) == [
        ("openrouter", "example/model", "description", 0)
    ]


def test_recorded_synthetic_requests_replay_exactly_through_real_adapter(
    frozen, tmp_path
):
    inputs, *_ = frozen
    selected = measure.selection(inputs, ("example/model",), (), 50, None)
    factory, _ = transport_factory(inputs)
    records = []

    def recording():
        inner = factory()

        def respond(request):
            response = inner.handle_request(request)
            records.append(
                (
                    request.method,
                    request.url.path,
                    measure.digest(request.content),
                    response.status_code,
                    response.content,
                )
            )
            return response

        return httpx.MockTransport(respond)

    assert (
        measure.execute(
            tmp_path / "first",
            inputs,
            selected,
            manifest(inputs),
            "secret",
            "1",
            "1",
            transport=recording,
        )["status"]
        == "complete"
    )
    expected = list(records)

    def replay():
        def respond(request):
            assert expected, "extra replay request"
            method, path, sha, status, body = expected.pop(0)
            assert (
                request.method,
                request.url.path,
                measure.digest(request.content),
            ) == (method, path, sha)
            return httpx.Response(status, content=body)

        return httpx.MockTransport(respond)

    assert (
        measure.execute(
            tmp_path / "replay",
            inputs,
            selected,
            manifest(inputs),
            "secret",
            "1",
            "1",
            transport=replay,
        )["status"]
        == "complete"
    )
    assert expected == [], "missing replay requests"
    expected[:] = records
    method, path, _, status, body = expected[2]
    expected[2] = (method, path, "0" * 64, status, body)
    refused = measure.execute(
        tmp_path / "altered",
        inputs,
        selected,
        manifest(inputs),
        "secret",
        "1",
        "1",
        transport=replay,
    )
    assert refused["status"] == "incomplete" and refused["public_evidence"] is None


def test_timeout_is_unknown_and_never_retried(frozen, tmp_path):
    inputs, *_ = frozen
    count = 0

    def factory():
        def timeout(request):
            nonlocal count
            count += 1
            raise httpx.ReadTimeout("private transport detail")

        return httpx.MockTransport(timeout)

    result = measure.execute(
        tmp_path / "run",
        inputs,
        measure.selection(inputs, ("example/model",), (), 50, None),
        manifest(inputs),
        "secret",
        "1",
        "1",
        transport=factory,
    )
    assert count == 1 and result["status"] == "incomplete"
    assert result["public_evidence"] is None
    assert (
        b"private transport detail"
        not in (tmp_path / "run/attempt-0/terminal.json").read_bytes()
    )


@pytest.mark.parametrize("omit_request_fee", [False, True])
def test_explicit_preflight_only_uses_capability_gets(
    frozen, tmp_path, monkeypatch, capsys, omit_request_fee
):
    inputs, database, artifacts, cohort = frozen
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(cohort.model_dump_json())
    cohort_path.chmod(0o600)
    key_path = tmp_path / "key.txt"
    key_path.write_text("synthetic-secret")
    key_path.chmod(0o600)
    factory, exchanges = transport_factory(inputs, omit_request_fee=omit_request_fee)
    reviewer = measure.OpenRouterReviewer
    monkeypatch.setattr(
        measure,
        "OpenRouterReviewer",
        lambda key, model: reviewer(key, model, transport=factory()),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight started contained process")

    monkeypatch.setattr(measure, "run_cover_packet_reviewer", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "measure",
            "--database",
            str(database),
            "--artifacts",
            str(artifacts),
            "--cohort",
            str(cohort_path),
            "--preflight",
            "--key-file",
            str(key_path),
            "--cover-model",
            "example/model",
            "--description-model",
            "example/model",
        ],
    )
    assert measure.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["capabilities"]) == 2
    assert [(method, path) for method, path, _ in exchanges] == [
        ("GET", "/api/v1/key"),
        ("GET", "/api/v1/models"),
        ("GET", "/api/v1/key"),
        ("GET", "/api/v1/models"),
    ]


@pytest.mark.parametrize("charge", ["-0.01", "1e100"])
@pytest.mark.parametrize("description", [False, True])
def test_invalid_charge_preserves_failed_final_evidence_and_stops(
    frozen, tmp_path, charge, description
):
    inputs, *_ = frozen
    factory, exchanges = transport_factory(
        inputs, charge=charge, invalid=True, omit_request_fee=description
    )
    directory = tmp_path / "unknown-charge"
    result = measure.execute(
        directory,
        inputs,
        measure.selection(
            inputs,
            () if description else ("example/model",),
            ("example/model",) if description else (),
            50,
            None,
        ),
        manifest(inputs),
        "secret",
        "1",
        "1",
        transport=factory,
    )
    assert result["status"] == "incomplete"
    assert result["requests_attempted"] == 1 and result["public_evidence"] is None
    assert [row[0] for row in exchanges] == ["GET", "GET", "POST"]
    assert (directory / "attempt-0/final-response.txt").read_bytes() == b"invalid"
    record = json.loads((directory / "attempt-0/exchange-2-terminal.json").read_bytes())
    assert record["charge_usd"] is None
    assert record["generation_id"] == "synthetic-generation"
    assert record["usage"] == {"prompt_tokens": 10}


def test_compressed_capabilities_and_final_response_use_real_parser(frozen, tmp_path):
    inputs, *_ = frozen
    factory, exchanges = transport_factory(inputs)

    def compressed_factory():
        delegate = factory()

        def respond(request):
            original = delegate.handle_request(request)
            compressed = gzip.compress(original.read())
            return httpx.Response(
                original.status_code,
                headers={
                    "content-encoding": "gzip",
                    "content-length": str(len(compressed)),
                },
                stream=httpx.ByteStream(compressed),
            )

        return httpx.MockTransport(respond)

    result = measure.execute(
        tmp_path / "compressed",
        inputs,
        measure.selection(inputs, ("example/model",), ("example/model",), 50, None),
        manifest(inputs),
        "secret",
        "1",
        "1",
        transport=compressed_factory,
    )
    assert result["status"] == "complete"
    assert [row[0] for row in exchanges] == ["GET", "GET", "POST"] * 4
    assert [
        (row["task"], row["requests_completed"])
        for row in result["public_evidence"]["models"]
    ] == [("cover", 2), ("description", 2)]


@pytest.mark.parametrize(
    "model,failure_at",
    [
        ("gpt-5.6-sol", None),
        ("different-model", None),
        ("gpt-5.6-sol", 0),
        ("gpt-5.6-sol", 1),
    ],
)
def test_contained_cli_model_admission_and_failure_stop(
    frozen, tmp_path, monkeypatch, capsys, model, failure_at
):
    inputs, database, artifacts, cohort = frozen
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(cohort.model_dump_json())
    cohort_path.chmod(0o600)
    profile_path = _contained_profile(tmp_path)
    output = tmp_path / "run"
    arguments = [
        "measure",
        "--database",
        str(database),
        "--artifacts",
        str(artifacts),
        "--cohort",
        str(cohort_path),
        "--codex-model",
        model,
        "--contained-profile",
        str(profile_path),
    ]
    monkeypatch.setattr("sys.argv", arguments)
    assert measure.main() == 0
    inventory = json.loads(capsys.readouterr().out)
    # Qualification is separately tested against a real wheel; retain the actual
    # CLI profile reader/parser, inventory, execute, observation parser and files.
    monkeypatch.setattr(measure, "installed_identity", lambda *args: manifest(inputs))
    calls = []

    def runner(profile, instruction, packet, schema, images):
        assert profile == profile_path.read_bytes()
        calls.append(packet)
        if len(calls) - 1 == failure_at:
            raise ValueError("synthetic contained failure")
        admitted = admit_cover_review_request(
            instruction.decode(), packet.decode(), schema.decode(), images
        )
        result = {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "observations": [
                {
                    "candidate_reference": row["reference"],
                    **{name: values[0][0] for name, values in TAXONOMY},
                }
                for row in json.loads(packet)["candidates"]
            ],
        }
        # The exact envelope shape is the response-binding contract under test.
        return measure.encoded(
            {
                "schema_version": "cover-contained-reviewer-result-v1",
                "status": "completed",
                "attempt_evidence": [{"dispatched": True}],
                "execution_sha256": measure.digest(
                    measure.encoded(
                        {
                            "profile_sha256": measure.digest(profile),
                            "request_sha256": admitted.digest,
                        }
                    )
                ),
                "request_sha256": admitted.digest,
                "input_sha256": measure.digest(packet),
                "image_count": len(images),
                "image_binding_sha256": measure.digest(
                    measure.encoded(
                        [
                            {"reference": reference, "sha256": sha}
                            for reference, sha, _ in images
                        ]
                    )
                ),
                "result": result,
                "result_sha256": measure.digest(measure.encoded(result)),
            }
        ).decode()

    monkeypatch.setattr(measure, "run_cover_packet_reviewer", runner)
    arguments += [
        "--execute",
        "--source-head",
        "a" * 40,
        "--wheel",
        str(tmp_path / "wheel"),
        "--receipt",
        str(tmp_path / "receipt"),
        "--installed-python",
        str(tmp_path / "python"),
        "--confirmed-cohort",
        inventory["cohort_sha256"],
        "--confirmed-inventory",
        inventory["inventory_sha256"],
        "--confirm-data-use",
        "--budget-target-usd",
        "2",
        "--stop-threshold-usd",
        "1.5",
        "--stop-on-failure",
        "--output",
        str(output),
    ]
    if model != "gpt-5.6-sol":
        with pytest.raises(ValueError, match="^contained model mismatch$"):
            measure.main()
        assert calls == [] and not output.exists()
        return
    assert measure.main() == (0 if failure_at is None else 2)
    summary = json.loads(capsys.readouterr().out)
    attempts = 2 if failure_at is None else failure_at + 1
    assert summary == {
        "status": "complete" if failure_at is None else "incomplete",
        "requests_attempted": attempts,
    }
    assert len(calls) == attempts
    assert not (output / f"attempt-{attempts}").exists()
    assert (
        json.loads((output / "manifest.json").read_bytes())["stop_on_failure"] is True
    )
    result = json.loads((output / "result.json").read_bytes())
    assert result["known_cost_usd"] == "0" and result["public_evidence"] is None
    for index in range(attempts):
        attempt = output / f"attempt-{index}"
        terminal = json.loads((attempt / "terminal.json").read_bytes())
        assert terminal["charge_state"] == "plan_allowance"
        assert terminal["charge_usd"] is None
        assert json.loads((attempt / "request.json").read_bytes())[
            "profile_sha256"
        ] == measure.digest(profile_path.read_bytes())
        assert terminal["completed"] is (index != failure_at)
        if index == failure_at:
            assert terminal["failure"] == "dispatch_or_parse_failed"
        else:
            assert terminal["parsed_result"] is not None
            assert terminal["attempt_evidence"] == [{"dispatched": True}]
            assert (attempt / "supported-envelope.json").is_file()
