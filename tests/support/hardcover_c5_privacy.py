"""Executable privacy corpus for the C5 safe-executor preview."""

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


FIXTURE_DIRECTORY = Path("tests/fixtures/hardcover_identity")
C5_RESOURCE_PATHS = (
    FIXTURE_DIRECTORY / "c5-safe-executor-input.json",
    FIXTURE_DIRECTORY / "c5-safe-executor-input-schema.json",
    FIXTURE_DIRECTORY / "c5-safe-executor-variant-matrix.json",
    FIXTURE_DIRECTORY / "c5-safe-executor-example-output.json",
    FIXTURE_DIRECTORY / "preview-output-schema-c5-v6.json",
    FIXTURE_DIRECTORY / "preview-output-privacy-contract-c5-v3.json",
    FIXTURE_DIRECTORY / "preview-output-privacy-scan-c5-v3.json",
    FIXTURE_DIRECTORY / "preview-registry-c5-v6.json",
)
C5_CAPTURE_CHANNELS = (
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
    "exports",
    "reports",
    "hash_inputs",
    "fingerprint_inputs",
)
C5_FORBIDDEN_SOURCE_CLASSES = (
    "contributor_control",
    "private_query",
    "isbn_identity",
    "provider_identity",
    "database_identity",
    "serialized_input",
    "resource_identity",
)


@dataclass(frozen=True)
class C5InputVariant:
    name: str
    source: dict[str, object]
    payload: bytes
    contributor_values: tuple[str, ...]


def c5_input_variants(repo_root: Path) -> tuple[C5InputVariant, ...]:
    source = json.loads(
        (repo_root / FIXTURE_DIRECTORY / "c5-safe-executor-input.json").read_text(
            encoding="utf-8"
        )
    )
    tainted = copy.deepcopy(source)
    collisions = copy.deepcopy(source)
    collision_words = (
        "pending",
        "matched",
        "failed",
        "claimed",
        "retry_wait",
        "succeeded",
        "retryable",
        "published",
        "started",
        "in_progress",
    )
    for index, case in enumerate(tainted["cases"]):
        case["label"] = f"wkxc5labeltaint{index:02d}"
        case["process"] = f"wkxc5proctaint{index:02d}"
    for index, case in enumerate(collisions["cases"]):
        case["label"] = collision_words[index]
        case["process"] = collision_words[-index - 1]
    variants = []
    for name, value in (
        ("canonical", source),
        ("taint-sentinel", tainted),
        ("internal-vocabulary-collision", collisions),
    ):
        contributor_values = tuple(
            control
            for case in value["cases"]
            for control in (case["label"], case["process"])
        )
        variants.append(
            C5InputVariant(
                name,
                value,
                json.dumps(value).encode("utf-8"),
                contributor_values,
            )
        )
    return tuple(variants)


def c5_forbidden_corpus(
    repo_root: Path,
    variants: Sequence[C5InputVariant],
    database_paths: Sequence[Path],
    *,
    allowed_values: Sequence[str],
    persistence_safe: bool = False,
) -> dict[str, frozenset[str]]:
    contributor = set()
    # Only the canonical and sentinel-bearing controls are taint sources. The
    # collision variant proves ordinary domain vocabulary is not mistaken for
    # contributor-derived data merely because equal text occurs internally.
    for variant in variants:
        if variant.name == "taint-sentinel":
            for value in variant.contributor_values:
                contributor.update(_text_projections(value, persistence_safe))
        elif variant.name == "canonical":
            for value in variant.contributor_values:
                contributor.update(
                    _blob_projections(value.encode("utf-8"), persistence_safe)
                )
    query = {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
    private_query = set(
        _blob_projections(json.dumps(query).encode("utf-8"), persistence_safe)
    )
    for value in query.values():
        private_query.update(
            _text_projections(
                value, persistence_safe, allow_numeric_prefix=value.isdigit()
            )
        )
    isbn_identity = set()
    for value in ("9780000000019", "0000000019"):
        isbn_identity.update(
            _blob_projections(value.encode("ascii"), persistence_safe)
        )
    provider_identity = set(
        _blob_projections(b"Remote Volume Zeta", persistence_safe)
        | _blob_projections(b'"id":1001', persistence_safe)
        | _blob_projections(b'"book_id":2001', persistence_safe)
    )
    database_identity = set()
    for path in database_paths:
        for value in (str(path), path.name, str(path.parent), path.parent.name):
            database_identity.update(_text_projections(value, persistence_safe))
    serialized_input = set()
    for variant in variants:
        serialized_input.update(
            _blob_projections(variant.payload, persistence_safe)
        )
    resource_identity = set()
    for relative in C5_RESOURCE_PATHS:
        payload = (repo_root / relative).read_bytes()
        for value in (relative.as_posix(), relative.name):
            resource_identity.update(
                _blob_projections(value.encode("utf-8"), persistence_safe)
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
        source_class: frozenset(
            projection
            for projection in projections
            if projection and projection not in allowed
        )
        for source_class, projections in corpus.items()
    }


def assert_c5_channels_private(
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
        expected_channels=C5_CAPTURE_CHANNELS,
        expected_source_classes=C5_FORBIDDEN_SOURCE_CLASSES,
    )
    persistence_channels = {
        channel: (values if channel == "persistence" else [])
        for channel, values in channels.items()
    }
    assert_projection_channels_private(
        persistence_channels,
        persistence_corpus,
        expected_channels=C5_CAPTURE_CHANNELS,
        expected_source_classes=C5_FORBIDDEN_SOURCE_CLASSES,
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


__all__ = [
    "C5_CAPTURE_CHANNELS",
    "C5_FORBIDDEN_SOURCE_CLASSES",
    "C5InputVariant",
    "assert_c5_channels_private",
    "c5_forbidden_corpus",
    "c5_input_variants",
]
