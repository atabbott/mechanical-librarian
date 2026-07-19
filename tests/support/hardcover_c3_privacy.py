"""Executable C3 privacy corpus for the fixed Hardcover adapter preview."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from tests.support.hardcover_c1_privacy import (
    PrivacyEvidenceError,
    assert_projection_channels_private,
    blob_projections,
    canonical_bytes,
    text_projections,
)


FIXTURE_DIRECTORY = Path("tests/fixtures/hardcover_identity")
C3_CAPTURE_CHANNELS = (
    "json_stdout",
    "text_stdout",
    "stderr",
    "exceptions",
    "structured_logs",
    "audit_logs",
    "traces",
    "spans",
    "metric_names",
    "metric_labels",
    "metric_values",
    "metric_exemplars",
    "database_rows",
    "exports",
    "reports",
    "hash_inputs",
    "fingerprint_inputs",
)
C3_FORBIDDEN_SOURCE_CLASSES = (
    "provider_title",
    "isbn_identity",
    "provider_identity",
    "request_response_transport",
    "serialized_input",
    "resource_identity",
)


@dataclass(frozen=True)
class C3InputVariant:
    name: str
    source: dict[str, object]
    payload: bytes


def c3_input_variants(repo_root: Path) -> tuple[C3InputVariant, ...]:
    payload = (
        repo_root / FIXTURE_DIRECTORY / "c3-fixed-adapter-input.json"
    ).read_bytes()
    canonical = _object(payload)
    title_changed = copy.deepcopy(canonical)
    title_document = _response_document(title_changed)
    rows = title_document["data"]["editions"]
    for row in rows:
        row["book"]["title"] = "WkxDifferentPrivateProviderTitleC3"
    title_body = canonical_bytes(title_document).rstrip(b"\n")
    title_changed["response"]["raw_body_bytes_utf8"] = title_body.decode("utf-8")
    _replace_content_length(title_changed, len(title_body))
    return (
        C3InputVariant("canonical", canonical, payload),
        C3InputVariant(
            "provider-title-alternate",
            title_changed,
            canonical_bytes(title_changed),
        ),
    )


def forbidden_source_corpus(
    repo_root: Path, *, variants: tuple[C3InputVariant, ...] | None = None
) -> dict[str, frozenset[str]]:
    executed = c3_input_variants(repo_root) if variants is None else variants
    if tuple(item.name for item in executed) != (
        "canonical",
        "provider-title-alternate",
    ):
        raise PrivacyEvidenceError("C3 executed input variants are incomplete")
    titles: set[str] = set()
    isbns: set[str] = set()
    identities: set[str] = set()
    transport_values: set[str] = set()
    for item in executed:
        request = item.source["request"]
        response = item.source["response"]
        transport = item.source["transport"]
        request_body = json.loads(request["body_bytes_utf8"])
        isbns.update(request_body["variables"]["isbns"])
        for row in _rows(item.source):
            titles.add(row["book"]["title"])
            for value in (
                row["id"],
                row["book_id"],
                row["canonical_id"],
                row["book"]["id"],
                row["book"]["canonical_id"],
            ):
                if value is not None:
                    identities.add(str(value))
        transport_values.update(
            str(value)
            for value in (
                request["body_bytes_utf8"],
                response["raw_status_line_bytes_utf8"],
                *response["raw_header_lines_bytes_utf8"],
                response["raw_body_bytes_utf8"],
                *transport.values(),
            )
        )
    resource_seeds: set[str] = set()
    for path in sorted((repo_root / FIXTURE_DIRECTORY).glob("*.json")):
        relative = path.relative_to(repo_root)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        resource_seeds.update(
            {
                relative.as_posix(),
                relative.name,
                digest,
                f"sha256:{digest}",
                f"resource:{relative.as_posix()}",
            }
        )
    corpus = {
        "provider_title": frozenset(
            projection for value in titles for projection in text_projections(value)
        ),
        "isbn_identity": frozenset(
            projection
            for value in isbns
            for projection in text_projections(value, allow_numeric_prefix=True)
        ),
        "provider_identity": frozenset(
            projection
            for value in identities
            for projection in text_projections(value, minimum_chunk_length=4)
        ),
        "request_response_transport": frozenset(
            projection
            for value in transport_values
            for projection in text_projections(value, minimum_chunk_length=8)
        ),
        "serialized_input": frozenset(
            projection
            for item in executed
            for projection in blob_projections(item.payload)
        ),
        "resource_identity": frozenset(
            projection
            for value in resource_seeds
            for projection in text_projections(value, minimum_chunk_length=8)
        ),
    }
    allowed = (
        repo_root / FIXTURE_DIRECTORY / "c3-fixed-adapter-example-output.json"
    ).read_text(encoding="utf-8")
    filtered = {
        source_class: frozenset(
            projection
            for projection in projections
            if projection and projection not in allowed
        )
        for source_class, projections in corpus.items()
    }
    if tuple(filtered) != C3_FORBIDDEN_SOURCE_CLASSES or any(
        not projections for projections in filtered.values()
    ):
        raise PrivacyEvidenceError("C3 forbidden source corpus is incomplete")
    return filtered


def assert_channels_private(
    channels: dict[str, list[str]], corpus: dict[str, frozenset[str]]
) -> None:
    assert_projection_channels_private(
        channels,
        corpus,
        expected_channels=C3_CAPTURE_CHANNELS,
        expected_source_classes=C3_FORBIDDEN_SOURCE_CLASSES,
    )


def _object(payload: bytes) -> dict[str, object]:
    value = json.loads(payload.decode("utf-8", "strict"))
    if not isinstance(value, dict):
        raise PrivacyEvidenceError("C3 input must be an object")
    return value


def _rows(source: dict[str, object]) -> list[dict[str, object]]:
    response = source.get("response")
    if not isinstance(response, dict) or type(response.get("raw_body_bytes_utf8")) is not str:
        raise PrivacyEvidenceError("C3 response is malformed")
    document = json.loads(response["raw_body_bytes_utf8"])
    rows = document["data"]["editions"]
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("book"), dict)
        for row in rows
    ):
        raise PrivacyEvidenceError("C3 rows are malformed")
    return rows


def _response_document(source: dict[str, object]) -> dict[str, object]:
    response = source["response"]
    return json.loads(response["raw_body_bytes_utf8"])


def _replace_content_length(source: dict[str, object], size: int) -> None:
    lines = source["response"]["raw_header_lines_bytes_utf8"]
    source["response"]["raw_header_lines_bytes_utf8"] = [
        f"Content-Length: {size}\r\n"
        if line.casefold().startswith("content-length:")
        else line
        for line in lines
    ]


__all__ = [
    "C3_CAPTURE_CHANNELS",
    "C3_FORBIDDEN_SOURCE_CLASSES",
    "C3InputVariant",
    "assert_channels_private",
    "c3_input_variants",
    "forbidden_source_corpus",
]
