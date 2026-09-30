#!/usr/bin/env python3
"""Owner-run frozen-cohort measurement. Inventory is local; spending is explicit.

Run with the retained qualified wheel's interpreter, outside the source checkout.
There is no scheduler, resume, tool retry, account mutation, or hard spending cap.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import sysconfig
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

# These validators remain owned by the exact-artifact qualifier. This tool never
# builds, installs or qualifies a wheel on an owner's behalf.
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_shadow_preview_repository,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterReviewer
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    unique_json_object,
)
from mechanical_librarian.application.cover_observation import observe_cover_packet
from mechanical_librarian.application.cover_packets import prepare_cover_packet
from mechanical_librarian.application.reviewer_contracts import (
    openrouter_cover_profile,
    openrouter_profile,
)
from mechanical_librarian.bootstrap.contained_reviewer import run_cover_packet_reviewer
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    DescriptionAdviceInput,
    DescriptionAdviceOutput,
    DescriptionPreferences,
    description_document,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.reviewer_access import (
    DescriptionModelMetadata,
    OpenRouterTaskSelection,
)
from mechanical_librarian.domain.reviewer_cost import ReviewerCostEvidence, per_thousand


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def encoded(value: object) -> bytes:
    return description_document(value).encode("utf-8")


def read_private(path: Path, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("private input permissions invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            value = stream.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError("private input too large")
        return value
    finally:
        os.close(descriptor)


def save(path: Path, value: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


class Cohort(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["reviewer-cost-cohort-v1"]
    report_ids: Annotated[
        tuple[Annotated[StrictStr, Field(min_length=1, max_length=128)], ...],
        Field(min_length=1, max_length=1000),
    ]

    @field_validator("report_ids")
    @classmethod
    def unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("duplicate report identity")
        return values


@dataclass(frozen=True)
class FrozenInput:
    report_id: str
    report_document: str
    cover_document: str | None
    images: tuple[tuple[str, str, bytes], ...]
    description_document: str | None
    cover_omissions: int

    def binding(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "report_sha256": digest(self.report_document.encode()),
            "cover_sha256": None
            if self.cover_document is None
            else digest(self.cover_document.encode()),
            "images": [
                {"reference": reference, "sha256": sha}
                for reference, sha, _ in self.images
            ],
            "description_sha256": None
            if self.description_document is None
            else digest(self.description_document.encode()),
            "cover_omissions": self.cover_omissions,
        }


def inventory(
    database: Path, artifacts: Path, cohort: Cohort
) -> tuple[FrozenInput, ...]:
    reader = open_shadow_preview_repository(database)
    store = CoverArtifactStore(artifacts, create=False)
    values = []
    try:
        for report_id in cohort.report_ids:
            reader._require_current()
            document = reader._reader.get(report_id)
            report = MetadataReport.from_json(document)
            if report.report_id != report_id:
                raise ValueError("report identity mismatch")
            cover = None
            images: tuple[tuple[str, str, bytes], ...] = ()
            omitted = 0
            if any(source.artifact is not None for source in report.cover_sources):

                def read_preview(
                    artifact: str, variant: str, report_id: str = report_id
                ) -> bytes:
                    return store.read_preview_digest(
                        reader._reader.preview_digest(report_id, artifact, variant)
                    )

                packet = prepare_cover_packet(document, read_preview)
                cover = packet.document
                images = tuple(
                    (image.reference, image.digest, image.payload)
                    for image in packet.images
                )
                omitted = packet.omitted_count
            try:
                description = description_document(
                    DescriptionAdviceInput.from_report(
                        report, DescriptionPreferences()
                    ).model_dump()
                )
            except ValueError:
                description = None
            reader._require_current()
            values.append(
                FrozenInput(report_id, document, cover, images, description, omitted)
            )
    finally:
        reader.close()
    return tuple(values)


def selection(
    inputs: tuple[FrozenInput, ...],
    cover_models: tuple[str, ...],
    description_models: tuple[str, ...],
    description_count: int,
    codex_model: str | None,
) -> list[tuple[str, str, str, int]]:
    if not 1 <= description_count <= 1000:
        raise ValueError("invalid description count")
    if len(set(cover_models)) != len(cover_models) or len(
        set(description_models)
    ) != len(description_models):
        raise ValueError("duplicate model")
    for model in (*cover_models, *description_models):
        OpenRouterTaskSelection(engine="openrouter", model=model)
    if len(cover_models) + len(description_models) > 100:
        raise ValueError("too many models")
    rows = [
        ("openrouter", model, "cover", index)
        for model in cover_models
        for index, value in enumerate(inputs)
        if value.cover_document is not None
    ]
    eligible = [
        index
        for index, value in enumerate(inputs)
        if value.description_document is not None
    ][:description_count]
    rows += [
        ("openrouter", model, "description", index)
        for model in description_models
        for index in eligible
    ]
    if codex_model is not None:
        rows += [
            ("codex", codex_model, "cover", index)
            for index, value in enumerate(inputs)
            if value.cover_document is not None
        ]
    return rows


def installed_identity(
    source: str, wheel: Path, receipt: Path, interpreter: Path
) -> dict[str, str]:
    import mechanical_librarian

    repository = Path(__file__).resolve().parents[1]
    for name in ("measure_reviewer_cost.py", "qualify_exact_artifact.py"):
        retained = subprocess.run(
            ["git", "-C", str(repository), "show", f"{source}:scripts/{name}"],
            check=True,
            capture_output=True,
        ).stdout
        if retained != (repository / "scripts" / name).read_bytes():
            raise ValueError("measurement source differs from qualified head")
    specification = importlib.util.spec_from_file_location(
        "reviewer_cost_qualifier", Path(__file__).with_name("qualify_exact_artifact.py")
    )
    assert specification is not None and specification.loader is not None
    qualifier = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = qualifier
    specification.loader.exec_module(qualifier)

    evidence = json.loads(receipt.read_bytes())
    wheel = wheel.resolve(strict=True)
    purelib = Path(sysconfig.get_path("purelib")).resolve()
    manifest = qualifier._validate_wheel(wheel, "mechanical-librarian")
    if (
        evidence.get("schema") != "exact-artifact-evidence-v4"
        or evidence.get("status") != "passed"
        or evidence.get("source_sha") != source
        or evidence.get("wheel_sha256") != digest(wheel.read_bytes())
        or evidence.get("wheel_payload_sha256") != manifest.payload_sha256
        or evidence.get("installed_payload_sha256") != manifest.payload_sha256
        or not all(
            evidence.get(name) is True
            for name in (
                "wheel_record_validated",
                "explicit_wheel_install",
                "outside_checkout",
                "required_suite_executed",
                "retained_wheel",
                "retained_environment",
            )
        )
        or Path(sys.executable).absolute() != interpreter.absolute()
        or mechanical_librarian.__file__ is None
        or not Path(mechanical_librarian.__file__).resolve().is_relative_to(purelib)
        or len(source) != 40
        or any(char not in "0123456789abcdef" for char in source)
    ):
        raise ValueError("installed qualification mismatch")
    qualifier._verify_installed_payload(
        wheel,
        purelib,
        "mechanical-librarian",
        manifest=manifest,
        console=(interpreter.parent / "mechanical-librarian").resolve(strict=True),
    )
    qualifier._verify_installed_origin(
        purelib / manifest.dist_info / "direct_url.json", wheel
    )
    return {
        "source_head": source,
        "wheel_sha256": evidence["wheel_sha256"],
        "payload_sha256": manifest.payload_sha256,
        "receipt_sha256": digest(receipt.read_bytes()),
    }


class Capture(httpx.BaseTransport):
    """One bounded exchange recorder; secrets and hidden reasoning never persist."""

    def __init__(self, delegate: httpx.BaseTransport, directory: Path, *, paid: bool):
        self.delegate, self.directory, self.paid = delegate, directory, paid
        self.exchanges: list[dict[str, Any]] = []
        self.charge: Decimal | None = None
        self.posts = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.url.host != "openrouter.ai"
            or request.url.scheme != "https"
            or (request.method, request.url.path)
            not in {
                ("GET", "/api/v1/key"),
                ("GET", "/api/v1/models"),
                ("POST", "/api/v1/chat/completions"),
            }
        ):
            raise ValueError("unsupported measurement exchange")
        if request.method == "POST":
            if not self.paid or self.posts:
                raise ValueError("paid dispatch not authorized")
            self.posts += 1
        binding = {
            "method": request.method,
            "path": request.url.path,
            "request_sha256": digest(request.content),
        }
        save(
            self.directory / f"exchange-{len(self.exchanges)}-start.json",
            encoded(binding),
        )
        self.exchanges.append(binding)
        response = self.delegate.handle_request(request)
        maximum = 1_048_576 if request.url.path == "/api/v1/models" else 131_072
        body = bytearray()
        try:
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > maximum:
                    raise ValueError("response exceeds capture bound")
        finally:
            response.close()
        record: dict[str, Any] = {**binding, "status": response.status_code}
        try:
            value = json.loads(
                body, object_pairs_hook=unique_json_object, parse_float=Decimal
            )
            if request.method == "POST":
                usage = value.get("usage")
                raw_cost = usage.get("cost") if type(usage) is dict else None
                if type(raw_cost) in (int, Decimal) and not isinstance(raw_cost, bool):
                    cost = str(raw_cost)
                    DescriptionModelMetadata.bounded_price(cost)
                    self.charge = Decimal(cost)
                record["generation_id"] = (
                    value.get("id")
                    if type(value.get("id")) is str and len(value["id"]) <= 256
                    else None
                )
                record["usage"] = {
                    name: usage[name]
                    for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if type(usage) is dict
                    and type(usage.get(name)) is int
                    and 0 <= usage[name] <= 1_000_000_000
                }
                choices = value.get("choices")
                if (
                    type(choices) is list
                    and len(choices) == 1
                    and type(choices[0]) is dict
                ):
                    message = choices[0].get("message")
                    content = message.get("content") if type(message) is dict else None
                    if type(content) is str and len(content.encode()) <= 65536:
                        secret = request.headers.get("authorization", "").removeprefix(
                            "Bearer "
                        )
                        if secret and secret in content:
                            raise ValueError("credential in final response")
                        save(self.directory / "final-response.txt", content.encode())
                record["charge_usd"] = None if self.charge is None else str(self.charge)
            else:
                # Capability bodies are not retained; bind their bounded bytes by digest.
                record["response_sha256"] = digest(bytes(body))
        except (ValueError, TypeError, AttributeError):
            self.charge = None
            record["capture"] = "unavailable"
        secret = request.headers.get("authorization", "").removeprefix("Bearer ")
        if secret and secret.encode() in encoded(record):
            self.charge = None
            record = {
                **binding,
                "status": response.status_code,
                "capture": "unavailable",
            }
        save(
            self.directory / f"exchange-{len(self.exchanges) - 1}-terminal.json",
            encoded(record),
        )
        self.exchanges[-1] = record
        return httpx.Response(
            response.status_code, headers=response.headers, content=bytes(body)
        )

    def close(self) -> None:
        self.delegate.close()


def execute(
    directory: Path,
    inputs: tuple[FrozenInput, ...],
    selected: list[tuple[str, str, str, int]],
    manifest: dict[str, Any],
    key: str,
    target: str,
    threshold: str,
    *,
    transport: Any = None,
    contained_profile: bytes | None = None,
) -> dict[str, Any]:
    budget, stop = Decimal(target), Decimal(threshold)
    if not budget.is_finite() or not stop.is_finite() or not 0 < stop <= budget:
        raise ValueError("invalid monitored budget")
    if not selected or len(selected) > 100_000:
        raise ValueError("empty or oversized selection")
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    save(
        directory / "manifest.json",
        encoded(
            {
                **manifest,
                "selected": selected,
                "budget_target_usd": target,
                "stop_threshold_usd": threshold,
                "budget_is_hard_cap": False,
            }
        ),
    )
    for index, value in enumerate(inputs):
        save(
            directory / f"input-{index}.json",
            encoded(
                {
                    **value.binding(),
                    "report_document": value.report_document,
                    "cover_document": value.cover_document,
                    "description_document": value.description_document,
                    "images": [
                        {
                            "reference": ref,
                            "sha256": sha,
                            "png_base64": base64.b64encode(payload).decode(),
                        }
                        for ref, sha, payload in value.images
                    ],
                }
            ),
        )
    known = Decimal(0)
    results: list[dict[str, Any]] = []
    complete = True
    for ordinal, (engine, model, task, index) in enumerate(selected):
        if known >= stop:
            complete = False
            break
        value = inputs[index]
        attempt = directory / f"attempt-{ordinal}"
        attempt.mkdir(mode=0o700)
        profile = (
            contained_profile
            if engine == "codex"
            else openrouter_cover_profile(model)
            if task == "cover"
            else openrouter_profile(model)
        )
        if profile is None:
            raise ValueError("missing contained profile")
        start = time.time_ns()
        save(
            attempt / "start.json",
            encoded(
                {
                    "engine": engine,
                    "model": model,
                    "task": task,
                    "input": index,
                    "started_at_ns": start,
                    "profile_sha256": digest(profile),
                }
            ),
        )
        recorder = None
        client = None
        output = None
        envelope = None
        failure = None
        try:
            if engine == "openrouter":
                recorder = Capture(
                    transport()
                    if transport
                    else httpx.HTTPTransport(retries=0, trust_env=False),
                    attempt,
                    paid=True,
                )
                client = OpenRouterReviewer(key, model, transport=recorder)

            def runner(
                p: bytes,
                instruction: bytes,
                packet: bytes,
                schema: bytes,
                images: tuple[tuple[str, str, bytes], ...],
                *,
                attempt=attempt,
                client=client,
                task=task,
            ) -> str:
                nonlocal envelope
                save(
                    attempt / "request.json",
                    encoded(
                        {
                            "profile_sha256": digest(p),
                            "instruction_sha256": digest(instruction),
                            "schema_sha256": digest(schema),
                            "packet_sha256": digest(packet),
                            "instruction": instruction.decode(),
                            "schema": schema.decode(),
                        }
                    ),
                )
                envelope = (
                    run_cover_packet_reviewer(p, instruction, packet, schema, images)
                    if client is None
                    else client.run(p, instruction, packet, schema, images)
                    if task == "cover"
                    else client.run_description(p, instruction, packet, schema)
                )
                if key and key in envelope:
                    envelope = None
                    raise ValueError("credential in supported response")
                save(attempt / "supported-envelope.json", envelope.encode())
                return envelope

            if task == "cover":
                assert value.cover_document is not None
                output = json.loads(
                    observe_cover_packet(
                        profile, value.cover_document.encode(), value.images, runner
                    ).document
                )
            else:
                assert value.description_document is not None
                request = ContainedReviewRequest(
                    DESCRIPTION_INSTRUCTION,
                    value.description_document,
                    DESCRIPTION_OUTPUT_SCHEMA,
                )
                document = runner(
                    profile,
                    request.instruction.encode(),
                    request.input_document.encode(),
                    request.output_schema.encode(),
                    (),
                )
                result = json.loads(document, object_pairs_hook=unique_json_object)
                if (
                    set(result) != {"request_sha256", "result"}
                    or result["request_sha256"] != request.digest
                ):
                    raise ValueError("description binding mismatch")
                output = DescriptionAdviceOutput.model_validate(
                    result["result"]
                ).model_dump()
        except Exception as error:
            from mechanical_librarian.application.contained_reviewer_contract import (
                ContainedReviewerError,
            )

            failure = "dispatch_or_parse_failed"
            if isinstance(error, ContainedReviewerError):
                save(
                    attempt / "contained-failure.json",
                    encoded(
                        {
                            "code": error.code,
                            "diagnostic_bytes": error.count,
                            "diagnostic_sha256": error.digest,
                        }
                    ),
                )
        finally:
            if client is not None:
                client.close()
        charge = None if recorder is None else recorder.charge
        terminal = {
            "engine": engine,
            "model": model,
            "task": task,
            "input": index,
            "completed": output is not None,
            "parsed_result": output,
            "failure": failure,
            "finished_at_ns": time.time_ns(),
            "latency_ns": time.time_ns() - start,
            "charge_usd": None if charge is None else str(charge),
            "charge_state": "plan_allowance"
            if engine == "codex"
            else "unknown"
            if charge is None
            else "known",
            "attempt_evidence": json.loads(envelope).get("attempt_evidence")
            if envelope is not None
            else None,
        }
        save(attempt / "terminal.json", encoded(terminal))
        results.append(terminal)
        if engine == "openrouter" and charge is None:
            complete = False
            break
        if charge is not None:
            known += charge
    complete = complete and len(results) == len(selected)
    result = {
        "status": "complete" if complete else "incomplete",
        "requests_selected": len(selected),
        "requests_attempted": len(results),
        "known_cost_usd": str(known),
        "public_evidence": None,
    }
    if complete:
        rows = []
        for model, task in dict.fromkeys(
            (row[1], row[2]) for row in selected if row[0] == "openrouter"
        ):
            attempts = [
                row
                for row in results
                if row["engine"] == "openrouter"
                and row["model"] == model
                and row["task"] == task
            ]
            total = sum((Decimal(row["charge_usd"]) for row in attempts), Decimal(0))
            rows.append(
                {
                    "model": model,
                    "task": task,
                    "requests_selected": len(attempts),
                    "requests_attempted": len(attempts),
                    "requests_completed": sum(row["completed"] for row in attempts),
                    "requests_failed": sum(not row["completed"] for row in attempts),
                    "works": len({row["input"] for row in attempts}),
                    "distinct_images": len(
                        {
                            sha
                            for row in attempts
                            for _, sha, _ in inputs[row["input"]].images
                        }
                    )
                    if task == "cover"
                    else 0,
                    "total_cost_usd": str(total),
                    "per_1000_usd": str(per_thousand(str(total), len(attempts))),
                }
            )
        if rows:
            evidence = ReviewerCostEvidence.model_validate_json(
                encoded(
                    {
                        "schema_version": "reviewer-cost-evidence-v1",
                        "measured_run": {
                            "source_head": manifest["source_head"],
                            "cohort_sha256": manifest["cohort_sha256"],
                            "observed_at": int(time.time()),
                        },
                        "models": rows,
                    }
                )
            )
            result["public_evidence"] = evidence.model_dump(mode="json")
    save(directory / "result.json", encoded(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--cohort", required=True, type=Path)
    parser.add_argument("--cover-model", action="append", default=[])
    parser.add_argument("--description-model", action="append", default=[])
    parser.add_argument("--description-count", type=int, default=50)
    parser.add_argument("--codex-model")
    parser.add_argument("--contained-profile", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--source-head")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--installed-python", type=Path)
    parser.add_argument("--confirmed-inventory")
    parser.add_argument("--confirmed-cohort")
    parser.add_argument("--confirm-data-use", action="store_true")
    parser.add_argument("--budget-target-usd")
    parser.add_argument("--stop-threshold-usd")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    cohort_bytes = read_private(arguments.cohort, 262144)
    cohort = Cohort.model_validate_json(cohort_bytes)
    inputs = inventory(arguments.database, arguments.artifacts, cohort)
    selected = selection(
        inputs,
        tuple(arguments.cover_model),
        tuple(arguments.description_model),
        arguments.description_count,
        arguments.codex_model,
    )
    inventory_document = {
        "inputs": [value.binding() for value in inputs],
        "selected": selected,
        "description_requested": arguments.description_count,
        "description_eligible": sum(
            value.description_document is not None for value in inputs
        ),
        "description_shortage": max(
            0,
            arguments.description_count
            - sum(value.description_document is not None for value in inputs),
        )
        if arguments.description_model
        else 0,
    }
    inventory_digest = digest(encoded(inventory_document))
    cohort_digest = digest(cohort_bytes)
    if not arguments.preflight and not arguments.execute:
        print(
            description_document(
                {
                    "cohort_sha256": cohort_digest,
                    "inventory_sha256": inventory_digest,
                    **inventory_document,
                }
            )
        )
        return 0
    if arguments.preflight:
        if arguments.codex_model:
            raise ValueError("preflight does not start contained processes")
        if arguments.key_file is None:
            raise ValueError("preflight requires explicit key file")
        key = read_private(arguments.key_file, 2048).decode().strip()
        statuses = []
        for task, models in (
            ("cover", arguments.cover_model),
            ("description", arguments.description_model),
        ):
            for model in models:
                client = OpenRouterReviewer(key, model)
                try:
                    statuses.append(
                        {
                            "task": task,
                            **asdict(
                                client.probe()
                                if task == "cover"
                                else client.probe_description()
                            ),
                        }
                    )
                finally:
                    client.close()
        print(
            description_document(
                {"inventory_sha256": inventory_digest, "capabilities": statuses}
            )
        )
        return 0
    if (
        arguments.confirmed_inventory != inventory_digest
        or arguments.confirmed_cohort != cohort_digest
        or not arguments.confirm_data_use
        or any(
            getattr(arguments, name) is None
            for name in (
                "source_head",
                "wheel",
                "receipt",
                "installed_python",
                "budget_target_usd",
                "stop_threshold_usd",
                "output",
            )
        )
    ):
        raise ValueError(
            "execute requires exact inventory, provenance, data use and budget confirmation"
        )
    identity = installed_identity(
        arguments.source_head,
        arguments.wheel,
        arguments.receipt,
        arguments.installed_python,
    )
    profile = None
    if arguments.codex_model:
        from mechanical_librarian.adapters.reviewers.contained_reviewer import (
            load_contained_reviewer_profile_bytes,
            read_required_contained_reviewer_profile,
        )

        if arguments.contained_profile is None:
            raise ValueError("contained profile required")
        profile = read_required_contained_reviewer_profile(arguments.contained_profile)
        if (
            load_contained_reviewer_profile_bytes(profile).model
            != arguments.codex_model
        ):
            raise ValueError("contained model mismatch")
    key = ""
    if arguments.cover_model or arguments.description_model:
        if arguments.key_file is None:
            raise ValueError("OpenRouter key required")
        key = read_private(arguments.key_file, 2048).decode().strip()
    result = execute(
        arguments.output,
        inputs,
        selected,
        {
            **identity,
            "cohort_sha256": cohort_digest,
            "inventory_sha256": inventory_digest,
            "inventory": inventory_document,
            "data_use_confirmed": True,
            "contained_local_abort_retry": "existing bounded retry; see attempt_evidence",
        },
        key,
        arguments.budget_target_usd,
        arguments.stop_threshold_usd,
        contained_profile=profile,
    )
    print(
        description_document(
            {
                "status": result["status"],
                "requests_attempted": result["requests_attempted"],
            }
        )
    )
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError):
        print(
            "Reviewer cost measurement refused; retained private evidence is unchanged.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
