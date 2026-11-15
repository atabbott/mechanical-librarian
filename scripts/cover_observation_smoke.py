"""Owner-run live smoke of the revised cover observations (#1417, ADR-0098).

A workbench instrument, deliberately not part of the installed product: run it
from the checkout an install was built from, against COPIES of that install's
``/data/application.sqlite3`` and ``/data/artifacts``. It opens both read-only
and changes neither.

Three steps, each its own command:

``preflight``
    Picks the covers to send and prices the run. It makes only OpenRouter's
    free key and catalogue requests, sends no cover to a model, and prints the
    model, the request count, the most one request can cost, the spending cap
    and a confirmation code.
``run``
    Sends the planned requests one at a time with ADR-0098's revised
    instruction and schema (``REVISED_CONTRACT``), after rebuilding the plan
    and checking it against the confirmation code. It stops before any request
    that could carry the known spend past the cap, when a charge is not
    reported, and after five failures in a row. It never retries. It writes a
    new private folder holding ``result.json`` (counts only, safe to share),
    each request's validated report, and a blind labelling sheet.
``score``
    Compares the owner's saved labels with the model's values and prints
    counts only. It sets no pass mark: qualifying a dimension belongs to #445.

The OpenRouter key is read from the copy's reviewer access and is never
printed or written. Nothing here reads an observation value to decide
anything; the revised vocabulary stays advisory and not current until the
activation pull request.

Usage:
    uv run python scripts/cover_observation_smoke.py preflight \\
        --database /copy/application.sqlite3 --artifacts /copy/artifacts \\
        [--requests 200] [--spend-cap-usd 5] [--model vendor/model]
    uv run python scripts/cover_observation_smoke.py run \\
        --database ... --artifacts ... [same options] \\
        --output /new/private/folder --confirm CODE
    uv run python scripts/cover_observation_smoke.py score \\
        --run /the/run/folder --labels ~/Downloads/cover-labels.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import shlex
import sqlite3
import stat
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote

import httpx

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_shadow_preview_repository,
)
from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterReviewer
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewerError,
    canonical_json,
    unique_json_object,
)
from mechanical_librarian.application.cover_observation import (
    REVISED_CONTRACT,
    observe_cover_packet,
)
from mechanical_librarian.application.cover_packets import prepare_cover_packet
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.reviewer_contracts import (
    OpenRouterAccountStatus,
    OpenRouterReviewerError,
    openrouter_cover_profile,
)
from mechanical_librarian.domain.reviewer_access import (
    CodexTaskSelection,
    OpenRouterTaskSelection,
)

RESULT_SCHEMA = "cover-observation-smoke-result-v1"
LABELS_SCHEMA = "cover-owner-labels-v1"
SCORE_SCHEMA = "cover-observation-label-score-v1"
DEFAULT_REQUESTS = 200
MAX_REQUESTS = 1000
DEFAULT_CAP_USD = "5"
MAX_CAP_USD = Decimal(100)
# The stable cover reviewer posture's completion ceiling (ADR-0062).
COMPLETION_TOKEN_CEILING = 8192
# A generous allowance per preview; previews are at most a few hundred pixels.
IMAGE_TOKEN_ALLOWANCE = 2000
PROMPT_OVERHEAD_TOKENS = 1000
CONSECUTIVE_FAILURE_LIMIT = 5
SHEET_LIMIT = 200
MIN_CONTROLS = 20
_ORIGIN_HOST = "openrouter.ai"
_ALLOWED_GETS = frozenset({"/api/v1/key", "/api/v1/models"})
_CHAT_PATH = "/api/v1/chat/completions"
_MAX_CHAT_CAPTURE = 1_048_576
_FREE_REFUSALS = frozenset({400, 401, 402, 403, 404, 429})

# The four rare dimensions the owner labels first (ADR-0098, "Before
# activation"). A value marks a cover when it is neither the ordinary value nor
# `unknown`.
RARE_ORDINARY: dict[str, frozenset[str]] = {
    "tie_in": frozenset({"absent", "unknown"}),
    "non_english_text": frozenset({"none", "unknown"}),
    "cover_image_kind": frozenset({"front_cover", "unknown"}),
    "promotional_text": frozenset({"absent", "minor", "unknown"}),
}
REVISED_VALUES: dict[str, tuple[str, ...]] = {
    name: tuple(value for value, _definition in values)
    for name, values in REVISED_CONTRACT.taxonomy
}


_REFUSAL_REMEDIES = {
    "invalid_key": "Check the OpenRouter key in Settings, then copy the database "
    "again.",
    "quota_exhausted": "Add credit to your OpenRouter account, then run the "
    "preflight again.",
    "rate_limited": "Wait a minute, then run the preflight again.",
    "model_unavailable": "Check the model id on OpenRouter, or choose another "
    "model with --model.",
    "model_text_only": "This model cannot read images. Choose another model with "
    "--model.",
    "data_policy_refused": "Allow this model in your OpenRouter privacy settings, "
    "or choose another model with --model.",
}
_STOPPED = {
    "spending_cap": "Stopped before a request that could pass the spending cap. "
    "Raise --spend-cap-usd or lower --requests, then run the preflight again.",
    "unknown_charge": "Stopped because OpenRouter did not report what a request "
    "cost. Check the charge in your OpenRouter activity.",
    "consecutive_failures": "Stopped after five failures in a row.",
    "interrupted": "Stopped because the run was interrupted.",
}


class SmokeRefused(Exception):
    """A plain-language refusal for the owner; nothing was sent or written."""


@dataclass(frozen=True)
class Item:
    report_id: str
    packet: str
    images: tuple[tuple[str, str, bytes], ...]

    def binding(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "packet_sha256": _digest(self.packet.encode("utf-8")),
            "images": [digest for _reference, digest, _payload in self.images],
        }


@dataclass(frozen=True)
class Population:
    items: tuple[Item, ...]
    current_items: int
    unprepared: int


@dataclass(frozen=True)
class Prices:
    prompt: Decimal
    completion: Decimal
    request: Decimal
    image: Decimal

    def document(self) -> dict[str, str]:
        return {
            "completion": str(self.completion),
            "image": str(self.image),
            "prompt": str(self.prompt),
            "request": str(self.request),
        }


@dataclass(frozen=True)
class Plan:
    model: str
    saved_model: bool
    prices: Prices
    cap: Decimal
    population: Population

    def ceiling(self, item: Item) -> Decimal:
        prompt_tokens = (
            len(REVISED_CONTRACT.instruction.encode("utf-8"))
            + len(item.packet.encode("utf-8"))
            + 4096  # the request's output schema, as an upper bound
            + IMAGE_TOKEN_ALLOWANCE * len(item.images)
            + PROMPT_OVERHEAD_TOKENS
        )
        return (
            self.prices.request
            + self.prices.image * len(item.images)
            + self.prices.prompt * prompt_tokens
            + self.prices.completion * COMPLETION_TOKEN_CEILING
        )

    def document(self) -> dict[str, object]:
        return {
            "cap_usd": str(self.cap),
            "instruction_sha256": REVISED_CONTRACT.instruction_sha256,
            "model": self.model,
            "output_schema_version": REVISED_CONTRACT.output_schema_version,
            "prices": self.prices.document(),
            "requests": [item.binding() for item in self.population.items],
            "saved_model": self.saved_model,
            "taxonomy_reference": REVISED_CONTRACT.taxonomy_reference,
        }

    @property
    def code(self) -> str:
        return _digest(canonical_json(self.document()).encode("utf-8"))[:16]


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SmokeRefused(f"No database copy at {path}.")
    absolute = str(path.absolute())
    connection = sqlite3.connect(
        f"file:{quote(absolute, safe='/')}?mode=ro&immutable=1", uri=True
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def reviewer_access(database: Path) -> tuple[str | None, bool, str | None]:
    """Return the saved OpenRouter cover model, whether Codex is saved, the key."""
    connection = _read_only_connection(database)
    try:
        document, key = ReviewerAccessStore(connection).snapshot()
    except Exception as error:
        raise SmokeRefused(
            "This copy's reviewer settings could not be read. Copy the database "
            "again with the app stopped."
        ) from error
    finally:
        connection.close()
    selection = document.cover_observation
    model = selection.model if isinstance(selection, OpenRouterTaskSelection) else None
    return model, isinstance(selection, CodexTaskSelection), key


def load_population(database: Path, artifacts: Path, limit: int) -> Population:
    """Current library items whose cover packet the product itself prepares."""
    if not 1 <= limit <= MAX_REQUESTS:
        raise SmokeRefused(f"--requests must be between 1 and {MAX_REQUESTS}.")
    if not artifacts.is_dir():
        raise SmokeRefused(f"No artifacts copy at {artifacts}.")
    connection = _read_only_connection(database)
    try:
        report_ids = sorted(
            {
                row[0]
                for row in connection.execute(
                    "SELECT report_id FROM current_review_packages"
                )
            },
            key=lambda value: _digest(value.encode("utf-8")),
        )
    except sqlite3.Error as error:
        raise SmokeRefused(
            "This copy is not a Mechanical Librarian application database."
        ) from error
    finally:
        connection.close()
    try:
        reader = open_shadow_preview_repository(database)
    except Exception as error:
        raise SmokeRefused(
            "This checkout cannot read the database copy. Check out the commit "
            "your install was built from, or copy the database again with the "
            "app stopped."
        ) from error
    try:
        store = CoverArtifactStore(artifacts, create=False)
    except Exception as error:
        reader.close()
        raise SmokeRefused(
            f"{artifacts} is not a copy of /data/artifacts. Copy it again with the "
            "app stopped."
        ) from error
    items: list[Item] = []
    unprepared = 0
    try:
        for report_id in report_ids:
            if len(items) == limit:
                break
            try:
                document = reader._reader.get(report_id)
            except (MetadataReportError, OSError, ValueError):
                unprepared += 1
                continue

            def read_preview(
                artifact: str, variant: str, report_id: str = report_id
            ) -> bytes:
                return store.read_preview_digest(
                    reader._reader.preview_digest(report_id, artifact, variant)
                )

            try:
                packet = prepare_cover_packet(document, read_preview)
            except (MetadataReportError, OSError, ValueError):
                unprepared += 1
                continue
            items.append(
                Item(
                    report_id,
                    packet.document,
                    tuple(
                        (image.reference, image.digest, image.payload)
                        for image in packet.images
                    ),
                )
            )
    finally:
        reader.close()
    return Population(tuple(items), len(report_ids), unprepared)


def prices_from(status: OpenRouterAccountStatus) -> Prices:
    if status.prompt_price is None or status.completion_price is None:
        raise SmokeRefused(
            f"OpenRouter lists no prompt or completion price for {status.model}, "
            "so the spending cap cannot be enforced. Choose another model."
        )
    return Prices(
        Decimal(status.prompt_price),
        Decimal(status.completion_price),
        Decimal(status.request_price or "0"),
        Decimal(status.image_price or "0"),
    )


def parse_cap(value: str) -> Decimal:
    try:
        cap = Decimal(value)
    except InvalidOperation:
        cap = Decimal(-1)
    if not cap.is_finite() or not 0 < cap <= MAX_CAP_USD:
        raise SmokeRefused(
            f"--spend-cap-usd must be above 0 and at most {MAX_CAP_USD}."
        )
    return cap


class ChargeTransport(httpx.BaseTransport):
    """Admit only OpenRouter's key, catalogue and one chat request; read the charge.

    No body is kept: the chat response is read once to find ``usage.cost``
    and handed back unchanged to the product's adapter.
    """

    def __init__(self, delegate: httpx.BaseTransport, *, paid: bool) -> None:
        self._delegate = delegate
        self._paid = paid
        self.posted = False
        self.charge: Decimal | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != _ORIGIN_HOST:
            raise httpx.UnsupportedProtocol("unsupported smoke exchange")
        if request.method == "GET" and request.url.path in _ALLOWED_GETS:
            return self._delegate.handle_request(request)
        if request.method != "POST" or request.url.path != _CHAT_PATH:
            raise httpx.UnsupportedProtocol("unsupported smoke exchange")
        if not self._paid or self.posted:
            raise httpx.UnsupportedProtocol("paid request not authorised")
        self.posted = True
        response = self._delegate.handle_request(request)
        body = bytearray()
        try:
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_CHAT_CAPTURE:
                    break
        finally:
            response.close()
        # A refusal before generation costs nothing; any other failure may
        # have been billed, so its charge is unknown and stops the run.
        if response.status_code == 200:
            self.charge = _charge(bytes(body))
        elif response.status_code in _FREE_REFUSALS:
            self.charge = Decimal(0)
        else:
            self.charge = None
        headers = response.headers.copy()
        for name in ("content-encoding", "content-length", "transfer-encoding"):
            headers.pop(name, None)
        return httpx.Response(
            response.status_code, headers=headers, content=bytes(body)
        )

    def close(self) -> None:
        self._delegate.close()


def _charge(body: bytes) -> Decimal | None:
    try:
        value = json.loads(
            body, object_pairs_hook=unique_json_object, parse_float=Decimal
        )
        usage = value.get("usage") if type(value) is dict else None
        cost = usage.get("cost") if type(usage) is dict else None
        if type(cost) is int:
            charge = Decimal(cost)
        elif type(cost) is Decimal:
            charge = cost
        else:
            return None
        return charge if charge.is_finite() and 0 <= charge <= MAX_CAP_USD else None
    except (TypeError, ValueError, UnicodeError):
        return None


TransportFactory = Callable[[], httpx.BaseTransport]


def _default_transport() -> httpx.BaseTransport:
    return httpx.HTTPTransport(retries=0, trust_env=False)


def build_plan(
    database: Path,
    artifacts: Path,
    *,
    requests: int,
    cap: Decimal,
    model: str | None,
    transport: TransportFactory,
) -> tuple[Plan, str]:
    saved_model, codex_saved, key = reviewer_access(database)
    if model is None:
        if codex_saved:
            raise SmokeRefused(
                "The saved cover reviewer uses Codex. Pass --model with an "
                "OpenRouter model to smoke it."
            )
        if saved_model is None:
            raise SmokeRefused(
                "No cover reviewer is saved in this copy. Pass --model with an "
                "OpenRouter model."
            )
        model = saved_model
    try:
        OpenRouterTaskSelection(engine="openrouter", model=model)
    except ValueError as error:
        raise SmokeRefused(
            f"{model!r} is not an OpenRouter model id. Copy the id from the "
            "model's OpenRouter page, such as vendor/model-name."
        ) from error
    if key is None:
        raise SmokeRefused(
            "No OpenRouter key is saved in this copy. Add one in Settings, then "
            "copy the database again."
        )
    population = load_population(database, artifacts, requests)
    if not population.items:
        raise SmokeRefused("No work in this copy has a cover to send.")
    recorder = ChargeTransport(transport(), paid=False)
    client = OpenRouterReviewer(key, model, transport=recorder)
    try:
        status = client.probe()
    except OpenRouterReviewerError as error:
        raise SmokeRefused(
            f"OpenRouter refused the model check ({error.code}). Nothing was sent. "
            + _REFUSAL_REMEDIES.get(error.code, "Try the preflight again later.")
        ) from error
    finally:
        client.close()
    if recorder.posted:  # pragma: no cover - the transport refuses it first
        raise SmokeRefused("A paid request was attempted during the preflight.")
    plan = Plan(model, model == saved_model, prices_from(status), cap, population)
    return plan, key


def _money(value: Decimal) -> str:
    """Dollars and cents, with up to four places for amounts under a cent."""
    if value != 0 and abs(value) < Decimal("0.01"):
        text = f"{value.quantize(Decimal('0.0001')):f}".rstrip("0")
        return "$" + (text if not text.endswith(".") else text + "00")
    return f"${value.quantize(Decimal('0.01')):,}"


def _price(value: Decimal) -> str:
    return "$" + f"{value.normalize():f}" if value else "$0"


def print_plan(plan: Plan, run_command: str, out: TextIO) -> None:
    population = plan.population
    images = sum(len(item.images) for item in population.items)
    ceilings = [plan.ceiling(item) for item in population.items]
    million = Decimal(1_000_000)
    whose = (
        "your saved cover reviewer" if plan.saved_model else "not your saved reviewer"
    )
    lines = [
        f"Model: {plan.model} ({whose})",
        "Prices: "
        f"{_price(plan.prices.prompt * million)} per million prompt tokens, "
        f"{_price(plan.prices.completion * million)} per million completion "
        f"tokens, {_price(plan.prices.image)} per image, "
        f"{_price(plan.prices.request)} per request",
        f"Requests: {len(population.items):,}, with {images:,} covers "
        f"({population.current_items:,} works in the library; "
        f"{population.unprepared:,} skipped because no cover could be prepared)",
        f"Worst case per request: {_money(max(ceilings))}",
        f"Worst case for all {len(population.items):,} requests: "
        f"{_money(sum(ceilings, Decimal(0)))}",
        f"Spending cap: {_money(plan.cap)}. The run stops before any request "
        "that could pass it.",
        f"Instruction digest: {REVISED_CONTRACT.instruction_sha256}",
        f"Confirmation code: {plan.code}",
        "",
        "Nothing has been sent to the model. To run it, replace NEW_FOLDER "
        "with a folder that does not exist yet:",
        f"  {run_command}",
    ]
    out.write("\n".join(lines) + "\n")


def _save(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _save_json(path: Path, value: object) -> None:
    _save(path, canonical_json(value).encode("utf-8"))


def _make_private_folder(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError as error:
        raise SmokeRefused(
            f"{path} already exists. Choose a new folder for each run."
        ) from error
    except FileNotFoundError as error:
        raise SmokeRefused(f"The folder above {path} does not exist.") from error
    path.chmod(0o700)


def _source_head() -> str | None:
    try:
        head = subprocess.run(
            [
                "git",
                "-C",
                str(Path(__file__).resolve().parents[1]),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return head if len(head) == 40 else None


@dataclass(frozen=True)
class Attempt:
    item: Item
    status: str  # "valid" | "failed"
    failure: str | None
    charge: Decimal | None  # None: sent, but its charge is unknown
    posted: bool
    report: dict[str, Any] | None


def _observe(plan: Plan, item: Item, key: str, transport: TransportFactory) -> Attempt:
    """Send one request; Ctrl-C is recorded with whatever was already paid."""
    recorder = ChargeTransport(transport(), paid=True)
    client = OpenRouterReviewer(key, plan.model, transport=recorder)
    report: dict[str, Any] | None = None
    failure: str | None = None
    try:
        document = observe_cover_packet(
            openrouter_cover_profile(plan.model),
            item.packet.encode("utf-8"),
            item.images,
            client.run,
            contract=REVISED_CONTRACT,
        ).document
        report = json.loads(document)
    except OpenRouterReviewerError as error:
        failure = error.code
    except ContainedReviewerError:
        failure = "request_rejected"
    except ValueError:
        failure = "output_rejected"
    except httpx.HTTPError:
        failure = "transport"
    except KeyboardInterrupt:
        failure = "interrupted"
    finally:
        client.close()
    return Attempt(
        item,
        "valid" if report is not None else "failed",
        failure,
        recorder.charge if recorder.posted else Decimal(0),
        recorder.posted,
        report,
    )


def run_smoke(
    plan: Plan,
    key: str,
    output: Path,
    *,
    transport: TransportFactory = _default_transport,
) -> dict[str, Any]:
    _make_private_folder(output)
    requests_folder = output / "requests"
    requests_folder.mkdir(mode=0o700)
    _save_json(
        output / "plan.json",
        {**plan.document(), "code": plan.code, "source_head": _source_head()},
    )
    spent = Decimal(0)
    attempts: list[Attempt] = []
    stopped: str | None = None
    consecutive = 0
    try:
        for ordinal, item in enumerate(plan.population.items):
            if spent + plan.ceiling(item) > plan.cap:
                stopped = "spending_cap"
                break
            attempt = _observe(plan, item, key, transport)
            attempts.append(attempt)
            _save_json(
                requests_folder / f"{ordinal:04d}.json",
                {
                    "charge_usd": None
                    if attempt.charge is None
                    else str(attempt.charge),
                    "failure": attempt.failure,
                    "ordinal": ordinal,
                    "paid_request_sent": attempt.posted,
                    "report": attempt.report,
                    "report_id": item.report_id,
                    "status": attempt.status,
                },
            )
            if attempt.charge is not None:
                spent += attempt.charge
            if attempt.failure == "interrupted":
                stopped = "interrupted"
                break
            if attempt.charge is None:
                stopped = "unknown_charge"
                break
            consecutive = consecutive + 1 if attempt.status == "failed" else 0
            if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                stopped = "consecutive_failures"
                break
    except KeyboardInterrupt:
        stopped = "interrupted"
    result = summarise(plan, attempts, spent, stopped)
    _save_json(output / "result.json", result)
    if any(attempt.report is not None for attempt in attempts):
        write_sheet(output, plan, attempts)
    return result


def summarise(
    plan: Plan, attempts: Sequence[Attempt], spent: Decimal, stopped: str | None
) -> dict[str, Any]:
    valid = [attempt for attempt in attempts if attempt.report is not None]
    failures = Counter(
        attempt.failure
        for attempt in attempts
        if attempt.failure not in (None, "interrupted")
    )
    counts: dict[str, Counter[str]] = {name: Counter() for name in REVISED_VALUES}
    covers = 0
    for attempt in valid:
        assert attempt.report is not None
        for row in attempt.report["observations"]:
            covers += 1
            for name in REVISED_VALUES:
                counts[name][row[name]] += 1
    unknown = sum(attempt.charge is None for attempt in attempts)
    selected = len(plan.population.items)
    if stopped is None and len(attempts) == selected and not failures:
        status = "passed"
    elif failures:
        status = "failed"
    else:
        status = "incomplete"
    return {
        "covers_observed": covers,
        "failures": dict(sorted(failures.items())),
        "instruction_sha256": REVISED_CONTRACT.instruction_sha256,
        "model": plan.model,
        "output_schema_version": REVISED_CONTRACT.output_schema_version,
        "plan_code": plan.code,
        "report_schema_version": REVISED_CONTRACT.report_schema_version,
        "reports_valid": len(valid),
        "requests_selected": selected,
        "requests_sent": len(attempts),
        "saved_model": plan.saved_model,
        "schema_version": RESULT_SCHEMA,
        "source_head": _source_head(),
        "spend_cap_usd": str(plan.cap),
        "spent_usd": str(spent),
        "status": status,
        "stopped": stopped,
        "taxonomy_reference": REVISED_CONTRACT.taxonomy_reference,
        "unknown_charges": unknown,
        "value_counts": {
            name: dict(sorted(counts[name].items())) for name in REVISED_VALUES
        },
    }


def _marked(values: dict[str, str]) -> bool:
    return any(values[name] not in RARE_ORDINARY[name] for name in RARE_ORDINARY)


def _marked_rarer(values: dict[str, str]) -> bool:
    return any(
        values[name] not in RARE_ORDINARY[name]
        for name in ("tie_in", "non_english_text", "cover_image_kind")
    )


def choose_sheet(covers: dict[str, dict[str, str]], seed: str) -> list[tuple[str, str]]:
    """Every marked cover, as many unmarked controls (at least 20), at most 200.

    When the sheet cannot hold every marked cover, half of it goes to controls
    and the rarer marks go first: prominent promotion is the commonest one.
    """
    chooser = random.Random(seed)
    marked = sorted(digest for digest, values in covers.items() if _marked(values))
    marked_set = set(marked)
    unmarked = sorted(digest for digest in covers if digest not in marked_set)
    chooser.shuffle(marked)
    chooser.shuffle(unmarked)
    marked.sort(key=lambda digest: not _marked_rarer(covers[digest]))
    room = (
        SHEET_LIMIT // 2
        if len(marked) > SHEET_LIMIT // 2
        else SHEET_LIMIT - len(marked)
    )
    controls = unmarked[: min(max(MIN_CONTROLS, len(marked)), room)]
    marked = marked[: SHEET_LIMIT - len(controls)]
    chosen = [(digest, "marked") for digest in marked] + [
        (digest, "control") for digest in controls
    ]
    chooser.shuffle(chosen)
    return chosen


def write_sheet(output: Path, plan: Plan, attempts: Sequence[Attempt]) -> None:
    """Write the blind labelling sheet: marked covers plus unmarked controls."""
    covers: dict[str, tuple[bytes, dict[str, str]]] = {}
    for attempt in attempts:
        if attempt.report is None:
            continue
        packet = json.loads(attempt.item.packet)
        by_reference = {
            candidate["reference"]: image
            for candidate, image in zip(
                packet["candidates"], attempt.item.images, strict=True
            )
        }
        for row in attempt.report["observations"]:
            _reference, digest, payload = by_reference[row["candidate_reference"]]
            covers.setdefault(
                digest, (payload, {name: row[name] for name in RARE_ORDINARY})
            )
    chosen = choose_sheet({d: v for d, (_p, v) in covers.items()}, plan.code)
    folder = output / "labelling"
    folder.mkdir(mode=0o700)
    (folder / "covers").mkdir(mode=0o700)
    manifest = []
    entries = []
    for number, (digest, stratum) in enumerate(chosen, 1):
        payload, values = covers[digest]
        _save(folder / "covers" / f"{number}.png", payload)
        manifest.append(
            {"cover": digest, "model": values, "number": number, "stratum": stratum}
        )
        entries.append(
            {"cover": digest, "image": f"covers/{number}.png", "number": number}
        )
    _save_json(folder / "sheet.json", {"plan_code": plan.code, "covers": manifest})
    _save(folder / "index.html", render_sheet(plan.code, entries).encode("utf-8"))


QUESTIONS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "tie_in",
        "Film or TV tie-in",
        (
            ("absent", "None"),
            ("franchise_text_only", "Named in text only"),
            ("adaptation_sticker_or_banner", "Sticker or banner on the book's art"),
            ("adaptation_art", "Film or TV art"),
            ("unknown", "Can't tell"),
        ),
    ),
    (
        "non_english_text",
        "Text not in English",
        (
            ("none", "None"),
            ("minor", "A minor line"),
            ("title_or_majority", "Title or most text"),
            ("unknown", "Can't tell"),
        ),
    ),
    (
        "cover_image_kind",
        "Kind of image",
        (
            ("front_cover", "Front cover"),
            ("designed_placeholder", "Placeholder or template"),
            ("non_cover_image", "Not a cover"),
            ("unknown", "Can't tell"),
        ),
    ),
    (
        "promotional_text",
        "Praise or selling claims",
        (
            ("absent", "None"),
            ("minor", "Minor"),
            ("prominent", "Prominent"),
            ("unknown", "Can't tell"),
        ),
    ),
)

_SHEET_STYLE = """
:root{color-scheme:light dark;--bg:#fbfaf8;--fg:#1d1b18;--muted:#5f5a52;
--line:#d9d4cc;--card:#ffffff;--accent:#2f5d8a;--accent-fg:#ffffff}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--fg:#ece8e1;
--muted:#a8a197;--line:#3a3732;--card:#211f1c;--accent:#8db7e0;--accent-fg:#10202f}}
*{box-sizing:border-box}
html{scroll-padding-top:8rem}
body{margin:0;background:var(--bg);color:var(--fg);
font:100%/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:1;background:var(--bg);
border-bottom:1px solid var(--line);padding:12px 16px;display:flex;flex-wrap:wrap;
gap:8px 16px;align-items:center;justify-content:space-between}
h1{font-size:1.25rem;margin:0}
header p{margin:0;color:var(--muted);flex-basis:100%}
.status{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid var(--accent);
background:var(--accent);color:var(--accent-fg);cursor:pointer}
#next{background:transparent;color:var(--accent)}
button:focus-visible,input:focus-visible+span{outline:3px solid var(--accent);
outline-offset:2px}
main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px}
article{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:16px;display:grid;grid-template-columns:minmax(120px,320px) 1fr;gap:16px}
figure{margin:0}
figure img{display:block;width:320px;max-width:100%;height:auto;
border:1px solid var(--line)}
figcaption{color:var(--muted);margin-top:4px}
fieldset{border:0;margin:0 0 12px;padding:0}
legend{font-weight:600;margin-bottom:4px}
label{display:inline-flex;align-items:center;gap:6px;margin:2px 14px 2px 0;
min-height:32px}
@media (max-width:640px){article{grid-template-columns:1fr}label{min-height:44px}}
@media (max-width:640px),(max-height:600px){header{position:static}
html{scroll-padding-top:0}}
"""

_SHEET_SCRIPT = """
const store = (() => {
  try { localStorage.setItem("probe", "1"); localStorage.removeItem("probe");
        return localStorage; } catch (error) { return null; }
})();
const key = "cover-labels-" + PLAN;
let labels = {};
try { labels = JSON.parse((store && store.getItem(key)) || "{}"); } catch (e) {}
const total = COVERS.length;
function done() {
  return COVERS.filter(c => QUESTIONS.every(q => labels[c.cover] &&
    labels[c.cover][q])).length;
}
function complete(c) {
  return QUESTIONS.every(q => labels[c.cover] && labels[c.cover][q]);
}
function show() {
  document.getElementById("progress").textContent =
    done() + " of " + total + " labelled";
}
document.getElementById("next").addEventListener("click", () => {
  const index = COVERS.findIndex(c => !complete(c));
  if (index < 0) return;
  const article = document.getElementById("cover-" + (index + 1));
  const open = Array.from(article.querySelectorAll("fieldset")).find(
    set => !set.querySelector("input:checked"));
  article.scrollIntoView();
  (open || article).querySelector("input").focus();
});
document.querySelectorAll("input[type=radio]").forEach(input => {
  const saved = labels[input.dataset.cover];
  if (saved && saved[input.dataset.question] === input.value) input.checked = true;
  input.addEventListener("change", () => {
    labels[input.dataset.cover] = labels[input.dataset.cover] || {};
    labels[input.dataset.cover][input.dataset.question] = input.value;
    if (store) { try { store.setItem(key, JSON.stringify(labels)); } catch (e) {} }
    document.getElementById("saved").textContent = "";
    show();
  });
});
document.getElementById("save").addEventListener("click", () => {
  const rows = COVERS.filter(c => labels[c.cover]).map(c =>
    Object.assign({cover: c.cover}, labels[c.cover]));
  const blob = new Blob([JSON.stringify({schema_version: SCHEMA, plan_code: PLAN,
    labels: rows}, null, 1)], {type: "application/json"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "cover-labels.json";
  document.body.appendChild(link); link.click(); link.remove();
  document.getElementById("saved").textContent =
    "Saved cover-labels.json to your downloads.";
});
show();
"""


def render_sheet(plan_code: str, entries: Sequence[dict[str, object]]) -> str:
    """A self-contained page: no network, no model values, labels stay local."""
    articles = []
    for entry in entries:
        number = int(str(entry["number"]))
        cover = html.escape(str(entry["cover"]), quote=True)
        fieldsets = []
        for name, legend, options in QUESTIONS:
            choices = "".join(
                f'<label><input type="radio" name="{name}-{number}" '
                f'value="{value}" data-cover="{cover}" data-question="{name}">'
                f"<span>{html.escape(text)}</span></label>"
                for value, text in options
            )
            fieldsets.append(
                f"<fieldset><legend>{html.escape(legend)}</legend>{choices}</fieldset>"
            )
        image = html.escape(str(entry["image"]), quote=True)
        articles.append(
            f'<article id="cover-{number}"><figure>'
            f'<a href="{image}" target="_blank">'
            f'<img src="{image}" alt="Cover {number}" loading="lazy"></a>'
            f"<figcaption>Cover {number}</figcaption></figure>"
            f"<div>{''.join(fieldsets)}</div></article>"
        )
    data = canonical_json([{"cover": entry["cover"]} for entry in entries]).replace(
        "</", "<\\/"
    )
    questions = canonical_json([name for name, _legend, _options in QUESTIONS])
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">"
        f"<title>Label covers</title><style>{_SHEET_STYLE}</style></head><body>"
        '<header><h1>Label covers</h1><div class="status">'
        '<span id="progress" aria-live="polite"></span>'
        '<button type="button" id="next">Next unanswered</button>'
        '<button type="button" id="save">Save labels</button>'
        '<span id="saved" aria-live="polite"></span></div>'
        "<p>Answer from the image alone. Your answers save in this browser as you "
        "go.</p></header>"
        f"<main>{''.join(articles)}</main>"
        f"<script>const PLAN={json.dumps(plan_code)};const SCHEMA="
        f"{json.dumps(LABELS_SCHEMA)};const COVERS={data};const QUESTIONS="
        f"{questions};{_SHEET_SCRIPT}</script></body></html>"
    )


def _read_json_file(path: Path, maximum: int) -> Any:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError
        return json.loads(path.read_bytes(), object_pairs_hook=unique_json_object)
    except (OSError, ValueError, UnicodeError) as error:
        raise SmokeRefused(
            f"{path} could not be read. Check the path, and use the file the "
            "labelling sheet saved."
        ) from error


_EDITED_LABELS = (
    "The labels file has been changed since the sheet saved it. Save the labels "
    "again from the sheet."
)


def _valid_sheet(sheet: object) -> bool:
    return (
        type(sheet) is dict
        and type(sheet.get("plan_code")) is str
        and type(sheet.get("covers")) is list
        and all(
            type(entry) is dict
            and type(entry.get("cover")) is str
            and entry.get("stratum") in ("marked", "control")
            and type(entry.get("model")) is dict
            and all(
                entry["model"].get(name) in REVISED_VALUES[name]
                for name in RARE_ORDINARY
            )
            for entry in sheet["covers"]
        )
    )


def score(run: Path, labels_path: Path) -> dict[str, Any]:
    sheet = _read_json_file(run / "labelling" / "sheet.json", 4_194_304)
    if not _valid_sheet(sheet):
        raise SmokeRefused(
            f"{run} is not a smoke run folder with a labelling sheet. Pass the "
            "folder given to --output."
        )
    labels = _read_json_file(labels_path, 4_194_304)
    if (
        type(labels) is not dict
        or labels.get("schema_version") != LABELS_SCHEMA
        or type(labels.get("labels")) is not list
    ):
        raise SmokeRefused(
            f"{labels_path} is not a labels file. Use the cover-labels.json that "
            "Save labels downloaded."
        )
    if labels.get("plan_code") != sheet["plan_code"]:
        raise SmokeRefused(
            "These labels belong to a different run. Pass the run folder whose "
            "sheet you labelled."
        )
    covers = {entry["cover"]: entry for entry in sheet["covers"]}
    tables: dict[str, dict[str, Counter[tuple[str, str]]]] = {
        name: {"marked": Counter(), "control": Counter()} for name in RARE_ORDINARY
    }
    labelled = 0
    seen: set[str] = set()
    for row in labels["labels"]:
        if (
            type(row) is not dict
            or type(row.get("cover")) is not str
            or row["cover"] not in covers
        ):
            raise SmokeRefused(
                "The labels name a cover that is not in this run. Pass the run "
                "folder whose sheet you labelled."
            )
        if row["cover"] in seen or set(row) - {"cover", *RARE_ORDINARY}:
            raise SmokeRefused(_EDITED_LABELS)
        seen.add(row["cover"])
        entry = covers[row["cover"]]
        answered = False
        for name in RARE_ORDINARY:
            owner = row.get(name)
            if owner is None:
                continue
            if owner not in REVISED_VALUES[name]:
                raise SmokeRefused(_EDITED_LABELS)
            tables[name][entry["stratum"]][(owner, entry["model"][name])] += 1
            answered = True
        labelled += answered
    return {
        "covers_in_sheet": len(covers),
        "covers_labelled": labelled,
        "dimensions": {
            name: {
                stratum: [
                    {"count": count, "model": model, "owner": owner}
                    for (owner, model), count in sorted(table.items())
                ]
                for stratum, table in strata.items()
            }
            for name, strata in tables.items()
        },
        "plan_code": sheet["plan_code"],
        "schema_version": SCORE_SCHEMA,
        "strata": dict(Counter(entry["stratum"] for entry in covers.values())),
    }


def _run_command(argv: Sequence[str], code: str) -> str:
    words = ["uv", "run", "python", "scripts/cover_observation_smoke.py", "run"]
    words += [shlex.quote(word) for word in argv[1:]]
    return " ".join(words) + f" --output NEW_FOLDER --confirm {code}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run"):
        command = commands.add_parser(name)
        command.add_argument("--database", required=True, type=Path)
        command.add_argument("--artifacts", required=True, type=Path)
        command.add_argument("--requests", type=int, default=DEFAULT_REQUESTS)
        command.add_argument("--spend-cap-usd", default=DEFAULT_CAP_USD)
        command.add_argument("--model")
        if name == "run":
            command.add_argument("--output", required=True, type=Path)
            command.add_argument("--confirm", required=True)
    scorer = commands.add_parser("score")
    scorer.add_argument("--run", required=True, type=Path)
    scorer.add_argument("--labels", required=True, type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    transport: TransportFactory = _default_transport,
) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "score":
        out.write(canonical_json(score(arguments.run, arguments.labels)) + "\n")
        return 0
    plan, key = build_plan(
        arguments.database,
        arguments.artifacts,
        requests=arguments.requests,
        cap=parse_cap(arguments.spend_cap_usd),
        model=arguments.model,
        transport=transport,
    )
    if arguments.command == "preflight":
        given = list(sys.argv[1:] if argv is None else argv)
        print_plan(plan, _run_command(given, plan.code), out)
        return 0
    if arguments.confirm != plan.code:
        raise SmokeRefused(
            "The confirmation code does not match this plan: the copy, the model's "
            "prices or an option changed. Run the preflight again. Nothing was sent."
        )
    result = run_smoke(plan, key, arguments.output, transport=transport)
    spent, cap = Decimal(result["spent_usd"]), Decimal(result["spend_cap_usd"])
    verdict = {
        "passed": "Smoke passed",
        "failed": "Smoke failed",
        "incomplete": "Smoke incomplete",
    }[result["status"]]
    spend = f"Spent {_money(spent)} of {_money(cap)}."
    if result["unknown_charges"]:
        spend = (
            f"Spent at least {_money(spent)} of {_money(cap)}; "
            f"{result['unknown_charges']} request's charge is unknown."
        )
    out.write(
        f"{verdict}: {result['reports_valid']} of {result['requests_selected']} "
        f"reports valid. {spend}\n"
    )
    if result["failures"]:
        out.write(
            "Failures: "
            + ", ".join(f"{count} {code}" for code, count in result["failures"].items())
            + ". The guide says what each means.\n"
        )
    if result["stopped"] is not None:
        out.write(_STOPPED[result["stopped"]] + "\n")
    if (arguments.output / "labelling" / "index.html").exists():
        out.write(f"Labelling sheet: {arguments.output / 'labelling' / 'index.html'}\n")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeRefused as refusal:
        print(str(refusal), file=sys.stderr)
        raise SystemExit(2) from None
