"""Executable C2 privacy corpus and channel scanner for Hardcover identity."""

from __future__ import annotations

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
C2_CAPTURE_CHANNELS = (
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
C2_FORBIDDEN_SOURCE_CLASSES = (
    "local_title",
    "provider_title",
    "isbn_identity",
    "serialized_private_query",
    "serialized_input",
    "provider_identity",
    "resource_identity",
)


@dataclass(frozen=True)
class C2InputVariant:
    name: str
    source: dict[str, object]
    input_payload: bytes
    query_payload: bytes
    local_title: str
    provider_titles: tuple[str, ...]
    isbns: tuple[str, ...]
    provider_identities: tuple[str, ...]


def c2_input_variants(repo_root: Path) -> tuple[C2InputVariant, ...]:
    canonical_payload = (
        repo_root / FIXTURE_DIRECTORY / "c2-identity-decision-input.json"
    ).read_bytes()
    canonical = _object(canonical_payload)
    title_alternate = _object(canonical_bytes(canonical))
    title_alternate["local_title"] = "WkxDifferentPrivateLocalTitle93"
    provider_alternate = _object(canonical_bytes(canonical))
    rows = provider_alternate.get("provider_rows")
    if not isinstance(rows, list) or not rows:
        raise PrivacyEvidenceError("C2 canonical provider rows are malformed")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("book"), dict):
            raise PrivacyEvidenceError("C2 canonical provider book is malformed")
        row["book"]["title"] = "WkxDifferentPrivateProviderTitle94"
    sources = (
        ("canonical", canonical, canonical_payload),
        ("title-only-alternate", title_alternate, canonical_bytes(title_alternate)),
        (
            "provider-title-alternate",
            provider_alternate,
            canonical_bytes(provider_alternate),
        ),
    )
    return tuple(_variant(name, source, payload) for name, source, payload in sources)


def forbidden_source_corpus(
    repo_root: Path, *, variants: tuple[C2InputVariant, ...] | None = None
) -> dict[str, frozenset[str]]:
    executed = c2_input_variants(repo_root) if variants is None else variants
    if tuple(item.name for item in executed) != (
        "canonical",
        "title-only-alternate",
        "provider-title-alternate",
    ):
        raise PrivacyEvidenceError("C2 executed input variants are incomplete")
    resource_seeds: set[str] = set()
    for path in sorted((repo_root / FIXTURE_DIRECTORY).glob("*.json")):
        relative = path.relative_to(repo_root)
        payload = (repo_root / relative).read_bytes()
        digest = __import__("hashlib").sha256(payload).hexdigest()
        resource_seeds.update(
            {
                relative.as_posix(),
                relative.name,
                digest,
                f"sha256:{digest}",
                f"resource:{relative.as_posix()}",
                f"fixture:{relative.as_posix()}",
            }
        )
        if "registry" in relative.name:
            resource_seeds.add(f"registry:{relative.as_posix()}")

    corpus = {
        "local_title": frozenset(
            projection
            for item in executed
            for projection in text_projections(item.local_title)
        ),
        "provider_title": frozenset(
            projection
            for item in executed
            for title in item.provider_titles
            for projection in text_projections(title)
        ),
        "isbn_identity": frozenset(
            projection
            for item in executed
            for value in item.isbns
            for projection in text_projections(value, allow_numeric_prefix=True)
        ),
        "serialized_private_query": frozenset(
            projection
            for item in executed
            for projection in blob_projections(item.query_payload)
        ),
        "serialized_input": frozenset(
            projection
            for item in executed
            for projection in blob_projections(item.input_payload)
        ),
        "provider_identity": frozenset(
            projection
            for item in executed
            for value in item.provider_identities
            for projection in text_projections(value, minimum_chunk_length=4)
        ),
        "resource_identity": frozenset(
            projection
            for seed in resource_seeds
            for projection in text_projections(seed, minimum_chunk_length=8)
        ),
    }
    if tuple(corpus) != C2_FORBIDDEN_SOURCE_CLASSES:
        raise PrivacyEvidenceError("C2 forbidden source classes are incomplete")
    allowed = (
        repo_root / FIXTURE_DIRECTORY / "c2-identity-decision-example-output.json"
    ).read_text(encoding="utf-8") + canonical_bytes(
        {
            "allowed_comparisons": ["different", "equal", "not_compared"],
            "allowed_outcomes": [
                "ambiguous",
                "incompatible",
                "malformed",
                "matched",
                "no_match",
            ],
        }
    ).decode("utf-8")
    filtered = {
        source_class: frozenset(
            projection
            for projection in projections
            if projection and projection not in allowed
        )
        for source_class, projections in corpus.items()
    }
    if any(not projections for projections in filtered.values()):
        raise PrivacyEvidenceError("C2 forbidden source class has no projections")
    return filtered


def assert_channels_private(
    channels: dict[str, list[str]], corpus: dict[str, frozenset[str]]
) -> None:
    assert_projection_channels_private(
        channels,
        corpus,
        expected_channels=C2_CAPTURE_CHANNELS,
        expected_source_classes=C2_FORBIDDEN_SOURCE_CLASSES,
    )


def _variant(name: str, source: dict[str, object], payload: bytes) -> C2InputVariant:
    canonical = source.get("canonical_isbn_13")
    derived = source.get("derived_isbn_10")
    local_title = source.get("local_title")
    rows = source.get("provider_rows")
    if (
        type(canonical) is not str
        or type(local_title) is not str
        or not isinstance(rows, list)
    ):
        raise PrivacyEvidenceError("C2 input variant is malformed")
    isbns = [canonical]
    if type(derived) is str:
        isbns.append(derived)
    titles: list[str] = []
    identities: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("book"), dict):
            raise PrivacyEvidenceError("C2 provider row is malformed")
        book = row["book"]
        title = book.get("title")
        if type(title) is not str:
            raise PrivacyEvidenceError("C2 provider title is malformed")
        titles.append(title)
        for label, value in (
            ("edition", row.get("id")),
            ("book", row.get("book_id")),
            ("edition-canonical", row.get("canonical_id")),
            ("book-canonical", book.get("canonical_id")),
        ):
            if value is not None:
                identities.extend((str(value), f"{label}:{value}"))
    query_payload = canonical_bytes(
        {"isbn": canonical, "title": local_title}
    ).rstrip(b"\n")
    return C2InputVariant(
        name,
        source,
        payload,
        query_payload,
        local_title,
        tuple(titles),
        tuple(isbns),
        tuple(identities),
    )


def _object(payload: bytes) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8", "strict"))
    except (UnicodeError, ValueError, RecursionError):
        raise PrivacyEvidenceError("C2 input variant is invalid JSON") from None
    if not isinstance(value, dict):
        raise PrivacyEvidenceError("C2 input variant must be an object")
    return value


__all__ = [
    "C2_CAPTURE_CHANNELS",
    "C2_FORBIDDEN_SOURCE_CLASSES",
    "C2InputVariant",
    "assert_channels_private",
    "c2_input_variants",
    "forbidden_source_corpus",
]
