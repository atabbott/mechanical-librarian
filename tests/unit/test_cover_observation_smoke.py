"""The owner-run cover observation smoke, against fakes only (#1417).

No test here reaches OpenRouter: every exchange goes to an ``httpx``
MockTransport, and the database and artifacts are synthetic.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts import cover_observation_smoke as smoke

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
)
from mechanical_librarian.adapters.persistence.schema import _register_casefold
from mechanical_librarian.application.cover_observation import (
    OUTPUT_SCHEMA_VERSION,
    REVISED_CONTRACT,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.reviewer_access import (
    REVIEWER_ACCESS_SCHEMA,
    CodexTaskSelection,
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
)
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.cover_producers import _preview, _source
from tests.unit.test_database_copy import other_unicode

KEY = "sk-or-synthetic-key-0123456789"
MODEL = "example/vision-model"


def _write_library(
    database: Path, artifacts: Path, selection: object | None, key: str | None
) -> list[str]:
    store = CoverArtifactStore(artifacts)
    repository = open_repository(database)
    base = _automatic_no_change_inputs("description_preserved")[0]
    report_ids = []
    try:
        for item, indexes in (
            ("item-a", (1, 2)),
            ("item-b", (3,)),
            ("item-c", ()),
            ("item-d", (4,)),
        ):
            sources = []
            for index in indexes:
                image = _preview(index)
                source = _source(index, image, incumbent=index in (1, 3, 4))
                assert source.artifact is not None
                store.stage(source.artifact, f"original-{index}".encode(), image)
                sources.append(source)
            report = MetadataReport.create(
                target=replace(base.target, item=item),
                file=base.file,
                local_claims=base.local_claims,
                local_reasons=base.local_reasons,
                providers=base.providers,
                audio=base.audio,
                cover_sources=tuple(sources),
            )
            repository.publish(report, lambda: True)
            report_ids.append(report.report_id)
    finally:
        repository.close()
    connection = sqlite3.connect(database, isolation_level=None)
    # A writer of an Attention input reads the record's pins (ADR-0108).
    _register_casefold(connection)
    try:
        # Publishing makes each item current; item-d leaves the library, so
        # its report is never sent.
        connection.execute("DELETE FROM current_review_packages WHERE item_id='item-d'")
        if selection is not None or key is not None:
            ReviewerAccessStore(connection).replace(
                ReviewerAccessDocument(
                    schema_version=REVIEWER_ACCESS_SCHEMA,
                    revision=2,
                    cover_observation=selection,  # type: ignore[arg-type]
                ),
                1,
                openrouter_key=key,
            )
    finally:
        connection.close()
    return report_ids


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, Path, list[str]]:
    database = tmp_path / "application.sqlite3"
    artifacts = tmp_path / "artifacts"
    report_ids = _write_library(
        database,
        artifacts,
        OpenRouterTaskSelection(engine="openrouter", model=MODEL),
        KEY,
    )
    return database, artifacts, report_ids


def _snapshot(database: Path, artifacts: Path) -> dict[str, bytes]:
    files = {
        str(path): path.read_bytes() for path in artifacts.rglob("*") if path.is_file()
    }
    files[str(database)] = database.read_bytes()
    return files


class Fake:
    """OpenRouter as a MockTransport, recording every exchange."""

    def __init__(
        self,
        *,
        charge: object = 0.001,
        content: Any = None,
        status: int = 200,
        pricing: dict[str, str] | None = None,
        values: dict[str, str] | None = None,
    ) -> None:
        self.exchanges: list[tuple[str, str]] = []
        self.bodies: list[bytes] = []
        self.charge = charge
        self.content = content
        self.status = status
        self.pricing = (
            {
                "prompt": "0.000001",
                "completion": "0.000002",
                "request": "0",
                "image": "0",
            }
            if pricing is None
            else pricing
        )
        self.values = values or {}

    def __call__(self) -> httpx.BaseTransport:
        return httpx.MockTransport(self.respond)

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.exchanges.append((request.method, request.url.path))
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json={"data": {"is_free_tier": False}})
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": MODEL,
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                            "supported_parameters": ["structured_outputs"],
                            "pricing": self.pricing,
                        }
                    ]
                },
            )
        assert request.method == "POST"
        self.bodies.append(request.content)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": self.status}})
        body = json.loads(request.content)
        packet = json.loads(
            body["messages"][0]["content"][0]["text"].split("\nSealed packet:\n")[1]
        )
        content = self.content
        if content is None:
            content = json.dumps(
                {
                    "schema_version": "cover-observer-output-v3",
                    "observations": [
                        {
                            "candidate_reference": row["reference"],
                            **{
                                name: self.values.get(name, values[0][0])
                                for name, values in REVISED_CONTRACT.taxonomy
                            },
                        }
                        for row in packet["candidates"]
                    ],
                }
            )
        usage = {} if self.charge is None else {"cost": self.charge}
        return httpx.Response(
            200,
            json={
                "id": "generation",
                "model": MODEL,
                "usage": usage,
                "choices": [{"message": {"content": content}}],
            },
        )

    @property
    def posts(self) -> int:
        return sum(method == "POST" for method, _path in self.exchanges)


def _main(args: list[str], fake: Fake) -> tuple[int, str]:
    out = io.StringIO()
    code = smoke.main(args, out=out, transport=fake)
    return code, out.getvalue()


def _base(library: tuple[Path, Path, list[str]]) -> list[str]:
    database, artifacts, _ = library
    return ["--database", str(database), "--artifacts", str(artifacts)]


def _code(text: str) -> str:
    match = re.search(r"Confirmation code: ([0-9a-f]{16})", text)
    assert match is not None
    return match.group(1)


def test_preflight_prices_the_plan_and_sends_nothing(library: Any) -> None:
    database, artifacts, _ = library
    before = _snapshot(database, artifacts)
    fake = Fake()
    code, text = _main(["preflight", *_base(library)], fake)
    assert code == 0
    assert fake.posts == 0
    assert set(fake.exchanges) == {("GET", "/api/v1/key"), ("GET", "/api/v1/models")}
    assert f"Model: {MODEL} (your saved cover reviewer)" in text
    assert "Requests: 2, with 3 covers (3 works in the library; 1 skipped" in text
    assert "Spending cap: $5.00." in text
    assert REVISED_CONTRACT.instruction_sha256 in text
    assert f"--confirm {_code(text)}" in text
    assert KEY not in text
    assert _snapshot(database, artifacts) == before


def test_population_is_current_items_in_a_stable_spread_order(library: Any) -> None:
    database, artifacts, report_ids = library
    population = smoke.load_population(database, artifacts, 10)
    assert population.current_items == 3
    assert population.unprepared == 1
    chosen = [item.report_id for item in population.items]
    assert set(chosen) == set(report_ids[:2])
    assert chosen == sorted(chosen, key=lambda value: smoke._digest(value.encode()))
    assert [
        item.report_id for item in smoke.load_population(database, artifacts, 1).items
    ] == chosen[:1]
    with pytest.raises(smoke.SmokeRefused):
        smoke.load_population(database, artifacts, 0)
    with pytest.raises(smoke.SmokeRefused):
        smoke.load_population(database, artifacts, smoke.MAX_REQUESTS + 1)


def test_a_copy_under_another_unicode_version_names_both_and_the_remedy(
    library: Any,
) -> None:
    database, artifacts, _ = library
    refusal = other_unicode(database)
    with pytest.raises(smoke.SmokeRefused) as raised:
        smoke.load_population(database, artifacts, 10)
    assert str(raised.value) == refusal


def _run(
    library: Any, tmp_path: Path, fake: Fake, *extra: str
) -> tuple[int, str, Path]:
    _code_value, text = _main(
        ["preflight", *_base(library), *extra], Fake(pricing=fake.pricing)
    )
    output = tmp_path / "run"
    code, run_text = _main(
        [
            "run",
            *_base(library),
            *extra,
            "--output",
            str(output),
            "--confirm",
            _code(text),
        ],
        fake,
    )
    return code, run_text, output


def test_run_passes_with_valid_bound_reports(library: Any, tmp_path: Path) -> None:
    database, artifacts, _ = library
    before = _snapshot(database, artifacts)
    fake = Fake()
    code, text, output = _run(library, tmp_path, fake)
    assert code == 0
    assert fake.posts == 2
    assert text.startswith("Smoke passed: 2 of 2 reports valid. Spent $0.002 of $5.00.")
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "passed"
    assert result["reports_valid"] == 2
    assert result["covers_observed"] == 3
    assert result["failures"] == {}
    assert result["instruction_sha256"] == REVISED_CONTRACT.instruction_sha256
    assert result["value_counts"]["tie_in"] == {"absent": 3}
    assert Decimal(result["spent_usd"]) == Decimal("0.002")
    assert (output.stat().st_mode & 0o777) == 0o700
    for path in output.rglob("*"):
        if path.is_file():
            assert (path.stat().st_mode & 0o777) == 0o600
            assert KEY.encode() not in path.read_bytes()
    reports = [
        json.loads(path.read_text()) for path in sorted((output / "requests").iterdir())
    ]
    assert {value["report"]["schema_version"] for value in reports} == {
        "cover-observation-report-v4"
    }
    assert _snapshot(database, artifacts) == before
    # Every chat request carried the revised instruction and schema.
    for body in fake.bodies:
        sent = json.loads(body)
        assert sent["messages"][0]["content"][0]["text"].startswith(
            REVISED_CONTRACT.instruction
        )
        schema = sent["response_format"]["json_schema"]["schema"]
        assert (
            schema["properties"]["schema_version"]["const"]
            == "cover-observer-output-v3"
        )


def test_result_json_holds_aggregates_only(library: Any, tmp_path: Path) -> None:
    _code_value, _text, output = _run(library, tmp_path, Fake())
    result = (output / "result.json").read_text()
    _database, _artifacts, report_ids = library
    assert not any(report_id in result for report_id in report_ids)
    assert "cover_ref_" not in result
    assert set(json.loads(result)) == {
        "covers_observed",
        "failures",
        "instruction_sha256",
        "model",
        "output_schema_version",
        "plan_code",
        "report_schema_version",
        "reports_valid",
        "requests_selected",
        "requests_sent",
        "saved_model",
        "schema_version",
        "source_head",
        "spend_cap_usd",
        "spent_usd",
        "status",
        "stopped",
        "taxonomy_reference",
        "unknown_charges",
        "value_counts",
    }


def test_rejected_output_fails_the_smoke(library: Any, tmp_path: Path) -> None:
    fake = Fake(
        content=json.dumps(
            {"schema_version": OUTPUT_SCHEMA_VERSION, "observations": []}
        )
    )
    code, text, output = _run(library, tmp_path, fake)
    assert code == 2
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "failed"
    assert result["failures"] == {"output_rejected": 2}
    assert "Failures: 2 output_rejected. The guide says what each means." in text
    assert not (output / "labelling").exists()


def test_unknown_charge_stops_the_run(library: Any, tmp_path: Path) -> None:
    fake = Fake(charge=None)
    code, text, output = _run(library, tmp_path, fake)
    assert code == 2
    assert fake.posts == 1
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "incomplete"
    assert result["stopped"] == "unknown_charge"
    assert "Stopped because OpenRouter did not report what a request cost." in text
    assert "Reported spend: $0.00 of $5.00. One request's charge is unknown." in text
    assert result["unknown_charges"] == 1


def test_cap_stops_before_a_request_that_could_pass_it(
    library: Any, tmp_path: Path
) -> None:
    # completion 8,192 tokens at $0.0001 = $0.8192 ceiling per request.
    pricing = {"prompt": "0", "completion": "0.0001", "request": "0", "image": "0"}
    fake = Fake(pricing=pricing, charge=0.5)
    code, _text, output = _run(library, tmp_path, fake, "--spend-cap-usd", "1.2")
    assert code == 2
    assert fake.posts == 1
    result = json.loads((output / "result.json").read_text())
    assert result["stopped"] == "spending_cap"
    assert result["requests_sent"] == 1


def _repeated_plan(library: Any, fake: Fake, copies: int) -> tuple[Any, str]:
    database, artifacts, _ = library
    plan, key = smoke.build_plan(
        database,
        artifacts,
        requests=1,
        cap=Decimal(5),
        model=None,
        transport=Fake(pricing=fake.pricing),
    )
    population = smoke.Population(plan.population.items * copies, 3, 0)
    return replace(plan, population=population), key


def test_five_failures_in_a_row_stop_the_run(library: Any, tmp_path: Path) -> None:
    fake = Fake(status=429)
    plan, key = _repeated_plan(library, fake, 7)
    result = smoke.run_smoke(plan, key, tmp_path / "run", transport=fake)
    assert fake.posts == smoke.CONSECUTIVE_FAILURE_LIMIT == 5
    assert result["stopped"] == "consecutive_failures"
    assert result["failures"] == {"rate_limited": 5}
    assert result["status"] == "failed"
    assert result["spent_usd"] == "0"
    assert len(list((tmp_path / "run" / "requests").iterdir())) == 5


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429])
def test_refusals_before_generation_cost_nothing(
    library: Any, tmp_path: Path, status: int
) -> None:
    fake = Fake(status=status)
    code, _text, output = _run(library, tmp_path, fake)
    assert code == 2
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "failed"
    assert result["spent_usd"] == "0"
    assert result["unknown_charges"] == 0
    assert sum(result["failures"].values()) == 2


@pytest.mark.parametrize("status", [408, 500, 502, 503])
def test_other_failures_leave_the_charge_unknown_and_stop(
    library: Any, tmp_path: Path, status: int
) -> None:
    fake = Fake(status=status)
    code, _text, output = _run(library, tmp_path, fake)
    assert code == 2
    assert fake.posts == 1
    result = json.loads((output / "result.json").read_text())
    assert result["stopped"] == "unknown_charge"
    assert result["unknown_charges"] == 1


def test_a_charge_above_its_ceiling_stops_the_run(library: Any, tmp_path: Path) -> None:
    fake = Fake(charge=1.0)
    code, text, output = _run(library, tmp_path, fake)
    assert code == 2
    assert fake.posts == 1
    result = json.loads((output / "result.json").read_text())
    assert result["stopped"] == "charge_above_ceiling"
    assert result["spent_usd"] == "1.0"
    assert "cost more than its worst case" in text


def test_image_allowance_covers_a_full_detail_preview_tile() -> None:
    # The dearest listed one-tile rate for a 320 x 480 preview.
    assert smoke.IMAGE_TOKEN_ALLOWANCE >= 2833 + 5667


def test_a_response_past_the_deadline_stops_with_an_unknown_charge(
    library: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake()
    _preflight, text = _main(["preflight", *_base(library)], Fake(pricing=fake.pricing))
    monkeypatch.setattr(smoke, "_CHAT_DEADLINE_SECONDS", -1.0)
    output = tmp_path / "run"
    code, _run_text = _main(
        ["run", *_base(library), "--output", str(output), "--confirm", _code(text)],
        fake,
    )
    assert code == 2
    assert fake.posts == 1
    result = json.loads((output / "result.json").read_text())
    assert result["stopped"] == "unknown_charge"
    assert result["unknown_charges"] == 1


class Interrupting(Fake):
    def respond(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.exchanges.append((request.method, request.url.path))
            raise KeyboardInterrupt
        return super().respond(request)


def test_ctrl_c_mid_request_is_recorded_with_an_unknown_charge(
    library: Any, tmp_path: Path
) -> None:
    fake = Interrupting()
    plan, key = _repeated_plan(library, fake, 3)
    result = smoke.run_smoke(plan, key, tmp_path / "run", transport=fake)
    assert fake.posts == 1
    assert result["stopped"] == "interrupted"
    assert result["status"] == "incomplete"
    assert result["requests_sent"] == 1
    assert result["unknown_charges"] == 1
    assert result["failures"] == {}
    record = json.loads((tmp_path / "run" / "requests" / "0000.json").read_text())
    assert record["paid_request_sent"] is True
    assert record["charge_usd"] is None
    assert record["failure"] == "interrupted"


class InterruptingBeforeSend(Fake):
    def respond(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            raise KeyboardInterrupt
        return super().respond(request)


def test_ctrl_c_before_sending_counts_no_request(library: Any, tmp_path: Path) -> None:
    fake = InterruptingBeforeSend()
    plan, key = _repeated_plan(library, fake, 3)
    result = smoke.run_smoke(plan, key, tmp_path / "run", transport=fake)
    assert fake.posts == 0
    assert result["stopped"] == "interrupted"
    assert result["status"] == "incomplete"
    assert result["requests_sent"] == 0
    assert result["unknown_charges"] == 0
    assert result["spent_usd"] == "0"


def test_a_missing_preview_is_counted_not_fatal(library: Any) -> None:
    database, artifacts, _ = library
    # item-b's only cover is preview 3; without it item-b cannot be prepared.
    previews = [
        path
        for path in artifacts.rglob("*")
        if path.is_file() and path.read_bytes() == _preview(3)
    ]
    assert len(previews) == 1
    previews[0].unlink()
    population = smoke.load_population(database, artifacts, 10)
    assert population.unprepared == 2
    assert len(population.items) == 1


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("0", "$0.00"),
        ("0.002", "$0.002"),
        ("0.00015", "$0.0002"),
        ("0.00002", "under $0.0001"),
        ("0.13", "$0.13"),
        ("5", "$5.00"),
        ("1234.5", "$1,234.50"),
    ],
    ids=["zero", "fraction", "tiny", "below-display", "cents", "whole", "thousands"],
)
def test_money_reads_as_dollars_and_cents(value: str, text: str) -> None:
    assert smoke._money(Decimal(value)) == text


def test_wrong_code_and_existing_folder_are_refused(
    library: Any, tmp_path: Path
) -> None:
    fake = Fake()
    with pytest.raises(smoke.SmokeRefused, match="confirmation code"):
        _main(
            [
                "run",
                *_base(library),
                "--output",
                str(tmp_path / "x"),
                "--confirm",
                "0" * 16,
            ],
            fake,
        )
    assert fake.posts == 0
    assert not (tmp_path / "x").exists()
    _code_value, text = _main(["preflight", *_base(library)], Fake())
    (tmp_path / "taken").mkdir()
    with pytest.raises(smoke.SmokeRefused, match="already exists"):
        _main(
            [
                "run",
                *_base(library),
                "--output",
                str(tmp_path / "taken"),
                "--confirm",
                _code(text),
            ],
            fake,
        )
    assert fake.posts == 0


def test_options_change_the_code(library: Any) -> None:
    codes = {
        _code(_main(["preflight", *_base(library), *extra], Fake())[1])
        for extra in ([], ["--requests", "1"], ["--spend-cap-usd", "4"])
    }
    assert len(codes) == 3


def test_codex_selection_needs_an_explicit_model(tmp_path: Path) -> None:
    database, artifacts = tmp_path / "db.sqlite3", tmp_path / "artifacts"
    _write_library(database, artifacts, CodexTaskSelection(engine="codex"), KEY)
    args = ["preflight", "--database", str(database), "--artifacts", str(artifacts)]
    with pytest.raises(smoke.SmokeRefused, match="uses Codex. Pass --model"):
        _main(args, Fake())
    code, text = _main([*args, "--model", MODEL], Fake())
    assert code == 0
    assert "(not your saved reviewer)" in text


def test_missing_key_or_selection_is_refused(tmp_path: Path) -> None:
    database, artifacts = tmp_path / "db.sqlite3", tmp_path / "artifacts"
    _write_library(database, artifacts, None, None)
    args = ["preflight", "--database", str(database), "--artifacts", str(artifacts)]
    with pytest.raises(smoke.SmokeRefused, match="No cover reviewer is saved"):
        _main(args, Fake())
    with pytest.raises(smoke.SmokeRefused, match="No OpenRouter key"):
        _main([*args, "--model", MODEL], Fake())


def test_a_model_without_prices_is_refused(library: Any) -> None:
    with pytest.raises(smoke.SmokeRefused, match="cannot be enforced"):
        _main(["preflight", *_base(library)], Fake(pricing={"image": "0"}))


@pytest.mark.parametrize(
    "cap",
    ["0", "-1", "101", "nan", "five"],
    ids=["zero", "negative", "large", "nan", "word"],
)
def test_invalid_caps_are_refused(cap: str) -> None:
    with pytest.raises(smoke.SmokeRefused):
        smoke.parse_cap(cap)


def test_the_transport_admits_one_paid_request_to_openrouter_only() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"usage": {"cost": 0.25}})

    paid = smoke.ChargeTransport(httpx.MockTransport(respond), paid=True)
    with httpx.Client(transport=paid) as client:
        client.post("https://openrouter.ai/api/v1/chat/completions", content=b"{}")
        assert paid.charge == Decimal("0.25")
        with pytest.raises(httpx.UnsupportedProtocol):
            client.post("https://openrouter.ai/api/v1/chat/completions", content=b"{}")
        with pytest.raises(httpx.UnsupportedProtocol):
            client.get("https://example.com/api/v1/key")
    free = smoke.ChargeTransport(httpx.MockTransport(respond), paid=False)
    with httpx.Client(transport=free) as client:
        with pytest.raises(httpx.UnsupportedProtocol):
            client.post("https://openrouter.ai/api/v1/chat/completions", content=b"{}")
    assert not free.posted


def test_sheet_is_blind_offline_and_holds_marked_and_controls(
    library: Any, tmp_path: Path
) -> None:
    fake = Fake(values={"tie_in": "adaptation_art"})
    code, text, output = _run(library, tmp_path, fake)
    assert code == 0
    page = (output / "labelling" / "index.html").read_text()
    sheet = json.loads((output / "labelling" / "sheet.json").read_text())
    assert f"Labelling sheet: {output / 'labelling' / 'index.html'}" in text
    assert {entry["stratum"] for entry in sheet["covers"]} == {"marked"}
    assert len(sheet["covers"]) == 3
    covers = json.loads(page.split("const COVERS=")[1].split(";const QUESTIONS")[0])
    assert [set(entry) for entry in covers] == [{"cover"}] * 3
    # Each article offers the option once; no model value is attached anywhere.
    assert page.count('value="adaptation_art"') == 3
    assert page.count("adaptation_art") == 3
    assert "stratum" not in page and "marked" not in page
    assert "http://" not in page and "https://" not in page
    assert "<title>Label covers</title>" in page
    for entry in sheet["covers"]:
        assert (output / "labelling" / "covers" / f"{entry['number']}.png").is_file()


def _values(**marks: str) -> dict[str, str]:
    return {
        "tie_in": "absent",
        "non_english_text": "none",
        "cover_image_kind": "front_cover",
        "promotional_text": "absent",
        **marks,
    }


def test_sheet_takes_every_mark_and_at_least_twenty_controls() -> None:
    covers = {f"{index:064x}": _values() for index in range(100)}
    covers.update(
        {f"{index:064x}": _values(tie_in="adaptation_art") for index in range(100, 105)}
    )
    covers[f"{105:064x}"] = _values(promotional_text="unknown")
    chosen = smoke.choose_sheet(covers, "seed")
    strata = [stratum for _digest, stratum in chosen]
    assert strata.count("marked") == 5
    assert strata.count("control") == 20
    assert chosen == smoke.choose_sheet(covers, "seed")
    assert len({digest for digest, _stratum in chosen}) == len(chosen)


def test_sheet_caps_at_two_hundred_and_prefers_rarer_marks() -> None:
    covers = {f"{index:064x}": _values() for index in range(400)}
    covers.update(
        {f"{index:064x}": _values(tie_in="adaptation_art") for index in range(400, 430)}
    )
    covers.update(
        {
            f"{index:064x}": _values(promotional_text="prominent")
            for index in range(430, 630)
        }
    )
    chosen = smoke.choose_sheet(covers, "seed")
    assert len(chosen) == smoke.SHEET_LIMIT
    marked = [digest for digest, stratum in chosen if stratum == "marked"]
    assert len(marked) == 100
    assert sum(covers[digest]["tie_in"] == "adaptation_art" for digest in marked) == 30


def test_score_counts_owner_against_model(library: Any, tmp_path: Path) -> None:
    fake = Fake(values={"tie_in": "adaptation_art"})
    _code_value, _text, output = _run(library, tmp_path, fake)
    sheet = json.loads((output / "labelling" / "sheet.json").read_text())
    covers = [entry["cover"] for entry in sheet["covers"]]
    labels = tmp_path / "cover-labels.json"
    labels.write_text(
        json.dumps(
            {
                "schema_version": "cover-owner-labels-v1",
                "plan_code": sheet["plan_code"],
                "labels": [
                    {
                        "cover": covers[0],
                        "tie_in": "adaptation_art",
                        "promotional_text": "minor",
                    },
                    {"cover": covers[1], "tie_in": "absent"},
                ],
            }
        )
    )
    code, text = _main(["score", "--run", str(output), "--labels", str(labels)], Fake())
    assert code == 0
    value = json.loads(text)
    assert value["covers_labelled"] == 2
    assert value["dimensions"]["tie_in"]["marked"] == [
        {"count": 1, "model": "adaptation_art", "owner": "absent"},
        {"count": 1, "model": "adaptation_art", "owner": "adaptation_art"},
    ]
    assert covers[0] not in text
    labels.write_text(
        json.dumps(
            {
                "schema_version": "cover-owner-labels-v1",
                "plan_code": sheet["plan_code"],
                "labels": [{"cover": "0" * 64, "tie_in": "absent"}],
            }
        )
    )
    with pytest.raises(smoke.SmokeRefused, match="not in this run"):
        _main(["score", "--run", str(output), "--labels", str(labels)], Fake())


def test_score_refuses_malformed_rows_plainly(library: Any, tmp_path: Path) -> None:
    _code_value, _text, output = _run(
        library, tmp_path, Fake(values={"tie_in": "adaptation_art"})
    )
    sheet = json.loads((output / "labelling" / "sheet.json").read_text())
    labels = tmp_path / "labels.json"
    for row in ({"cover": ["x"]}, {"cover": {"a": 1}}, "text"):
        labels.write_text(
            json.dumps(
                {
                    "schema_version": "cover-owner-labels-v1",
                    "plan_code": sheet["plan_code"],
                    "labels": [row],
                }
            )
        )
        with pytest.raises(smoke.SmokeRefused):
            smoke.score(output, labels)
    (output / "labelling" / "sheet.json").chmod(0o600)
    other = tmp_path / "other"
    (other / "labelling").mkdir(parents=True)
    (other / "labelling" / "sheet.json").write_text('{"covers": 1}')
    with pytest.raises(smoke.SmokeRefused, match="not a smoke run folder"):
        smoke.score(other, labels)
