"""C1-scoped privacy corpus for the C6 lifecycle preview."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tests.support.hardcover_c1_privacy import (
    assert_projection_channels_private,
    blob_projections,
    text_projections,
)


FIXTURES = Path("tests/fixtures/hardcover_identity")
C6_RESOURCE_PATHS = (
    FIXTURES / "c6-lifecycle-orchestration-input.json",
    FIXTURES / "c6-lifecycle-orchestration-input-schema.json",
    FIXTURES / "c6-lifecycle-orchestration-variant-matrix.json",
    FIXTURES / "c6-lifecycle-orchestration-example-output.json",
    FIXTURES / "preview-output-schema-c6-v1.json",
    FIXTURES / "preview-output-privacy-contract-c6-v1.json",
    FIXTURES / "preview-output-privacy-scan-c6-v1.json",
    FIXTURES / "preview-registry-c6-v1.json",
)
C6_CAPTURE_CHANNELS = (
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
    "persistence",
    "file_names",
    "file_paths",
    "exports",
    "reports",
    "runtime_evidence",
    "hash_inputs",
    "fingerprint_inputs",
)
C6_FORBIDDEN_SOURCE_CLASSES = (
    "contributor_control",
    "private_query",
    "isbn_identity",
    "provider_identity",
    "database_identity",
    "serialized_input",
    "resource_identity",
)


@dataclass(frozen=True)
class C6InputVariant:
    name: str
    source: dict[str, object]
    payload: bytes
    contributor_values: tuple[str, ...]


def c6_input_variants(repo_root: Path) -> tuple[C6InputVariant, ...]:
    source = json.loads((repo_root / C6_RESOURCE_PATHS[0]).read_text("utf-8"))
    tainted = copy.deepcopy(source)
    collision = copy.deepcopy(source)
    vocabulary = (
        "pending",
        "matched",
        "claimed",
        "retry_wait",
        "cancelled",
        "interrupted",
    )
    for index, case in enumerate(tainted["cases"]):
        case["label"] = f"wkxc6labeltaint{index}"
        case["process"] = f"wkxc6proctaint{index}"
    for index, case in enumerate(collision["cases"]):
        case["label"] = vocabulary[index]
        case["process"] = vocabulary[-index - 1]
    variants = []
    for name, value in (
        ("canonical", source),
        ("taint-sentinel", tainted),
        ("internal-vocabulary-collision", collision),
    ):
        variants.append(
            C6InputVariant(
                name,
                value,
                json.dumps(value).encode("utf-8"),
                tuple(
                    control
                    for case in value["cases"]
                    for control in (case["label"], case["process"])
                ),
            )
        )
    return tuple(variants)


def c6_forbidden_corpus(
    repo_root: Path,
    variants: Sequence[C6InputVariant],
    database_paths: Sequence[Path],
    *,
    allowed_values: Sequence[str],
    persistence_safe: bool = False,
) -> dict[str, frozenset[str]]:
    contributor = set()
    for variant in variants:
        if variant.name in {"canonical", "taint-sentinel"}:
            for value in variant.contributor_values:
                contributor.update(_text_projections(value, persistence_safe))
    query = {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
    private_query = set(
        _blob_projections(json.dumps(query).encode("utf-8"), persistence_safe)
    )
    for value in query.values():
        private_query.update(
            _text_projections(
                value,
                persistence_safe,
                allow_numeric_prefix=value.isdigit(),
            )
        )
    isbn_identity = set()
    for value in ("9780000000019", "0000000019"):
        isbn_identity.update(
            _blob_projections(value.encode("ascii"), persistence_safe)
        )
    provider_identity = set()
    for value in (b"Remote Volume Zeta", b'"id":1001', b'"book_id":2001'):
        provider_identity.update(_blob_projections(value, persistence_safe))
    database_identity = set(
        _text_projections("private-c6-sentinel.sqlite", persistence_safe)
    )
    for path in database_paths:
        for value in (str(path), path.name, str(path.parent), path.parent.name):
            database_identity.update(_text_projections(value, persistence_safe))
    serialized_input = set()
    for variant in variants:
        serialized_input.update(
            _blob_projections(variant.payload, persistence_safe)
        )
    resource_identity = set()
    for relative in C6_RESOURCE_PATHS:
        payload = (repo_root / relative).read_bytes()
        resource_identity.update(
            _blob_projections(relative.as_posix().encode(), persistence_safe)
        )
        resource_identity.update(
            _blob_projections(relative.name.encode(), persistence_safe)
        )
        resource_identity.update(_blob_projections(payload, persistence_safe))
    corpus = {
        "contributor_control": contributor,
        "private_query": private_query,
        "isbn_identity": isbn_identity,
        "provider_identity": provider_identity,
        "database_identity": database_identity,
        "serialized_input": serialized_input,
        "resource_identity": resource_identity,
    }
    allowed = "\n".join(allowed_values)
    return {
        source: frozenset(value for value in values if value and value not in allowed)
        for source, values in corpus.items()
    }


def assert_c6_channels_private(
    channels: Mapping[str, list[str]],
    corpus: Mapping[str, frozenset[str]],
    *,
    persistence_corpus: Mapping[str, frozenset[str]] | None = None,
) -> None:
    if persistence_corpus is None:
        persistence_corpus = corpus
    runtime_channels = {
        channel: ([] if channel == "persistence" else values)
        for channel, values in channels.items()
    }
    assert_projection_channels_private(
        runtime_channels,
        corpus,
        expected_channels=C6_CAPTURE_CHANNELS,
        expected_source_classes=C6_FORBIDDEN_SOURCE_CLASSES,
    )
    persistence_channels = {
        channel: (values if channel == "persistence" else [])
        for channel, values in channels.items()
    }
    assert_projection_channels_private(
        persistence_channels,
        persistence_corpus,
        expected_channels=C6_CAPTURE_CHANNELS,
        expected_source_classes=C6_FORBIDDEN_SOURCE_CLASSES,
    )


def _blob_projections(payload: bytes, persistence_safe: bool) -> frozenset[str]:
    projections = blob_projections(payload)
    if not persistence_safe:
        return projections
    return projections - _short_digest_prefixes(payload)


def _text_projections(
    value: str,
    persistence_safe: bool,
    *,
    allow_numeric_prefix: bool = False,
) -> frozenset[str]:
    projections = text_projections(
        value, allow_numeric_prefix=allow_numeric_prefix
    )
    if not persistence_safe:
        return projections
    return projections - _short_digest_prefixes(value.encode("utf-8"))


def _short_digest_prefixes(payload: bytes) -> frozenset[str]:
    digesters = (
        hashlib.md5(payload, usedforsecurity=False),
        hashlib.sha1(payload, usedforsecurity=False),
        hashlib.sha224(payload),
        hashlib.sha256(payload),
        hashlib.sha384(payload),
        hashlib.sha512(payload),
        hashlib.sha3_256(payload),
        hashlib.sha3_512(payload),
        hashlib.blake2s(payload),
        hashlib.blake2b(payload),
    )
    return frozenset(digester.hexdigest()[:8] for digester in digesters)


def resource_digests(repo_root: Path) -> tuple[str, ...]:
    return tuple(
        hashlib.sha256((repo_root / path).read_bytes()).hexdigest()
        for path in C6_RESOURCE_PATHS
    )


__all__ = [
    "C6_CAPTURE_CHANNELS",
    "C6_FORBIDDEN_SOURCE_CLASSES",
    "C6InputVariant",
    "assert_c6_channels_private",
    "c6_forbidden_corpus",
    "c6_input_variants",
    "resource_digests",
]
