"""Executable C1-only public privacy evidence for the inert Hardcover report."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, quote_plus

from mechanical_librarian.domain.hardcover_identity import validate_private_query
from mechanical_librarian.domain.title_proposals import normalized_title_key


class PrivacyEvidenceError(ValueError):
    """The committed C1 privacy evidence is inconsistent or leaks a source."""


FIXTURE_DIRECTORY = Path("tests/fixtures/hardcover_identity")
OUTPUT_SCHEMA_PATH = FIXTURE_DIRECTORY / "c1-inert-report-output-schema-v1.json"
PRIVACY_CONTRACT_PATH = FIXTURE_DIRECTORY / "c1-inert-report-privacy-contract-v1.json"
PRIVACY_SCAN_PATH = FIXTURE_DIRECTORY / "c1-inert-report-privacy-scan-v1.json"
REGISTRY_PATH = FIXTURE_DIRECTORY / "c1-inert-report-registry-v1.json"

RESOURCE_PATHS = (
    Path("docs/reference/hardcover-config-v1.json"),
    Path("docs/reference/hardcover-private-query-v1.json"),
    Path("docs/reference/hardcover-private-query-v1.schema.json"),
    FIXTURE_DIRECTORY / "c1-inert-report-input.json",
    FIXTURE_DIRECTORY / "c1-inert-report-input-schema.json",
    FIXTURE_DIRECTORY / "c1-inert-report-variant-matrix.json",
    FIXTURE_DIRECTORY / "c1-inert-report-example-output.json",
    OUTPUT_SCHEMA_PATH,
    PRIVACY_CONTRACT_PATH,
)

CAPTURE_CHANNELS = (
    "stdout",
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
    "non_audit_persistence",
    "exports",
    "reports",
    "hash_inputs",
    "fingerprint_inputs",
)
ALLOWED_RESULT_KEYS = (
    "database_opened",
    "network_started",
    "outcome",
    "private_file_opened",
    "secret_opened",
)
FORBIDDEN_RESULT_KEY_FRAGMENTS = (
    "isbn",
    "title",
    "query",
    "book_id",
    "edition_id",
    "input",
    "hash",
    "digest",
    "fingerprint",
    "identifier",
    "correlation",
    "path",
    "request",
    "response",
    "header",
)
FORBIDDEN_SOURCE_CLASSES = (
    "title",
    "canonical_isbn",
    "serialized_private_query",
    "serialized_input",
    "provider_identity",
    "resource_identity",
)
_FUTURE_MARKERS = (
    "identity-decision-v3",
    "fixed-adapter-v3",
    "durable-work-v3",
    "safe-executor-v3",
    "schema8",
    "all five",
)

TITLE_ONLY_ALTERNATE = "WkxDifferentConfidentialName91"
ALTERNATE_ISBN = "0306406152"


@dataclass(frozen=True)
class C1InputVariant:
    """One exact synthetic input and its separately serialized private query."""

    name: str
    source: dict[str, object]
    input_payload: bytes
    query_payload: bytes
    raw_isbn: str
    canonical_isbn: str
    title: str


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def c1_input_variants(repo_root: Path) -> tuple[C1InputVariant, ...]:
    canonical_path = FIXTURE_DIRECTORY / "c1-inert-report-input.json"
    canonical_payload = (repo_root / canonical_path).read_bytes()
    canonical_source = _decode_json(canonical_payload)
    title_alternate = _decode_json(canonical_bytes(canonical_source))
    title_alternate_query = title_alternate["private_query"]
    if not isinstance(title_alternate_query, dict):
        raise PrivacyEvidenceError("C1 canonical private query is malformed")
    title_alternate_query["title"] = TITLE_ONLY_ALTERNATE
    isbn_alternate = _decode_json(canonical_bytes(canonical_source))
    isbn_alternate_query = isbn_alternate["private_query"]
    if not isinstance(isbn_alternate_query, dict):
        raise PrivacyEvidenceError("C1 canonical private query is malformed")
    isbn_alternate_query["isbn"] = ALTERNATE_ISBN
    sources = (
        ("canonical", canonical_source, canonical_payload),
        ("title-only-alternate", title_alternate, canonical_bytes(title_alternate)),
        ("isbn-alternate", isbn_alternate, canonical_bytes(isbn_alternate)),
    )
    variants: list[C1InputVariant] = []
    for name, source, input_payload in sources:
        private_query = source.get("private_query")
        if not isinstance(private_query, Mapping):
            raise PrivacyEvidenceError("C1 input private query is malformed")
        raw_isbn = private_query.get("isbn")
        title = private_query.get("title")
        if type(raw_isbn) is not str or type(title) is not str:
            raise PrivacyEvidenceError("C1 input private query is malformed")
        validated = validate_private_query(private_query)
        variants.append(
            C1InputVariant(
                name=name,
                source=source,
                input_payload=input_payload,
                query_payload=canonical_bytes(private_query).rstrip(b"\n"),
                raw_isbn=raw_isbn,
                canonical_isbn=validated.canonical_isbn,
                title=title,
            )
        )
    return tuple(variants)


def output_schema_document() -> dict[str, object]:
    return {
        "$id": "hardcover-c1-inert-report-output-v1",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "result": {
                "additionalProperties": False,
                "properties": {
                    "database_opened": {"const": False},
                    "network_started": {"const": False},
                    "outcome": {"enum": ["disabled", "invalid_input"]},
                    "private_file_opened": {"const": False},
                    "secret_opened": {"const": False},
                },
                "required": list(ALLOWED_RESULT_KEYS),
                "type": "object",
            },
            "scenario": {"const": "inert-report-v3"},
            "schema": {"const": "hardcover-synthetic-output-v5"},
        },
        "required": ["schema", "scenario", "result"],
        "type": "object",
    }


def privacy_contract_document() -> dict[str, object]:
    return {
        "allowed_result_keys": list(ALLOWED_RESULT_KEYS),
        "captured_channels": list(CAPTURE_CHANNELS),
        "channel_invariant": (
            "every C1 forbidden source and projection is absent from every "
            "captured runtime channel"
        ),
        "forbidden_result_key_fragments": list(FORBIDDEN_RESULT_KEY_FRAGMENTS),
        "forbidden_source_classes": list(FORBIDDEN_SOURCE_CLASSES),
        "scenario": "inert-report-v3",
        "schema": "hardcover-c1-inert-report-privacy-v1",
        "scope": {
            "future_scenarios_included": False,
            "installed_increment": (
                "Install the inert F09f configuration and disabled identity report"
            ),
            "runtime_schema": "hardcover-synthetic-output-v5",
        },
        "source_projection_contract": {
            "bounded_meaningful_token_and_chunk_projections": True,
            "executed_input_variants": [
                "canonical",
                "title-only-alternate",
                "isbn-alternate",
            ],
            "exact_raw_input_and_query_serializations_included": True,
            "length_projections_are_contextual": True,
            "resource_seed_kinds": [
                "path",
                "name",
                "digest",
                "sha256-prefixed-digest",
                "resource-prefixed-path",
                "fixture-prefixed-path",
                "registry-prefixed-path",
            ],
            "resource_seeds_use_text_projections": True,
        },
        "title_only_metamorphic_contract": {
            "exported_fields_unchanged": True,
            "hash_or_fingerprint_calls_with_title_projection": 0,
            "stable_exported_identifier": False,
            "title_or_projection_in_any_channel": 0,
        },
    }


def artifact_descriptor(repo_root: Path, relative_path: Path) -> dict[str, object]:
    payload = (repo_root / relative_path).read_bytes()
    return {
        "bytes": len(payload),
        "path": relative_path.as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def build_registry(repo_root: Path) -> dict[str, object]:
    resources = {
        "canonical_example_input": artifact_descriptor(
            repo_root, FIXTURE_DIRECTORY / "c1-inert-report-input.json"
        ),
        "canonical_example_output": artifact_descriptor(
            repo_root, FIXTURE_DIRECTORY / "c1-inert-report-example-output.json"
        ),
        "input_schema": artifact_descriptor(
            repo_root, FIXTURE_DIRECTORY / "c1-inert-report-input-schema.json"
        ),
        "output_schema": artifact_descriptor(repo_root, OUTPUT_SCHEMA_PATH),
        "privacy_contract": artifact_descriptor(repo_root, PRIVACY_CONTRACT_PATH),
        "privacy_scan": artifact_descriptor(repo_root, PRIVACY_SCAN_PATH),
        "variant_matrix": artifact_descriptor(
            repo_root, FIXTURE_DIRECTORY / "c1-inert-report-variant-matrix.json"
        ),
    }
    return {
        "authority": (
            "no query file, secret, provider, client, resolver, proxy, transport, "
            "socket, network, or database authority"
        ),
        "argv": [
            "mechanical-librarian",
            "hardcover-identity-report",
            "--mode",
            "synthetic-preview",
            "--scenario",
            "inert-report-v3",
            "--input",
            "-",
            "--format",
            "json",
        ],
        "resources": resources,
        "scenario": "inert-report-v3",
        "schema": "hardcover-c1-inert-report-registry-v1",
        "scope": {
            "future_scenarios_included": False,
            "predecessor_registry_sha256": None,
        },
    }


def build_scan(repo_root: Path, *, verify_registry: bool = True) -> dict[str, object]:
    _verify_source_documents(repo_root)
    variants = c1_input_variants(repo_root)
    artifacts = {
        path.as_posix(): artifact_descriptor(repo_root, path)
        for path in RESOURCE_PATHS
    }
    if verify_registry:
        registry = _load_canonical(repo_root / REGISTRY_PATH)
        if registry != build_registry(repo_root):
            raise PrivacyEvidenceError("C1 registry does not match its resources")
    aggregate = {
        "artifacts": artifacts,
        "generated_path": PRIVACY_SCAN_PATH.as_posix(),
        "forbidden_result_key_fragments": list(FORBIDDEN_RESULT_KEY_FRAGMENTS),
        "forbidden_source_classes": list(FORBIDDEN_SOURCE_CLASSES),
        "executed_input_variants": [variant.name for variant in variants],
        "registry_path": REGISTRY_PATH.as_posix(),
        "result_allowlist": list(ALLOWED_RESULT_KEYS),
        "scenario": "inert-report-v3",
        "schema": "hardcover-c1-inert-report-privacy-scan-v1",
        "scope": {
            "future_scenarios_included": False,
            "present_tree_only": True,
        },
    }
    _reject_future_markers(canonical_bytes(aggregate))
    return aggregate


def validate_output_document(source: object) -> None:
    if not isinstance(source, Mapping) or set(source) != {
        "schema",
        "scenario",
        "result",
    }:
        raise PrivacyEvidenceError("C1 output has unknown or missing fields")
    if (
        source.get("schema") != "hardcover-synthetic-output-v5"
        or source.get("scenario") != "inert-report-v3"
    ):
        raise PrivacyEvidenceError("C1 output identity is invalid")
    result = source.get("result")
    if not isinstance(result, Mapping) or set(result) != set(ALLOWED_RESULT_KEYS):
        raise PrivacyEvidenceError("C1 result is outside its allowlist")
    for key in result:
        folded = key.casefold()
        if any(fragment in folded for fragment in FORBIDDEN_RESULT_KEY_FRAGMENTS):
            raise PrivacyEvidenceError("C1 result key contains a forbidden fragment")
    if result.get("outcome") not in {"disabled", "invalid_input"}:
        raise PrivacyEvidenceError("C1 outcome is invalid")
    for key in set(ALLOWED_RESULT_KEYS) - {"outcome"}:
        if type(result.get(key)) is not bool or result.get(key) is not False:
            raise PrivacyEvidenceError("C1 access counter is not strict false")


def forbidden_source_corpus(
    repo_root: Path, *, variants: tuple[C1InputVariant, ...] | None = None
) -> dict[str, frozenset[str]]:
    executed = c1_input_variants(repo_root) if variants is None else variants
    if tuple(variant.name for variant in executed) != (
        "canonical",
        "title-only-alternate",
        "isbn-alternate",
    ):
        raise PrivacyEvidenceError("C1 executed input variants are incomplete")
    resource_seeds: set[str] = set()
    for path in (*RESOURCE_PATHS, PRIVACY_SCAN_PATH, REGISTRY_PATH):
        payload = (repo_root / path).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        resource_seeds.update(
            {
                path.as_posix(),
                path.name,
                digest,
                digest[:8],
                f"sha256:{digest}",
                f"resource:{path.as_posix()}",
            }
        )
        if path == REGISTRY_PATH:
            resource_seeds.add(f"registry:{path.as_posix()}")
        if path.is_relative_to(FIXTURE_DIRECTORY):
            resource_seeds.add(f"fixture:{path.as_posix()}")
    corpus = {
        "title": frozenset(
            projection
            for variant in executed
            for projection in text_projections(variant.title)
        ),
        "canonical_isbn": frozenset(
            projection
            for variant in executed
            for value in (variant.raw_isbn, variant.canonical_isbn)
            for projection in text_projections(value, allow_numeric_prefix=True)
        ),
        "serialized_private_query": frozenset(
            projection
            for variant in executed
            for projection in blob_projections(variant.query_payload)
        ),
        "serialized_input": frozenset(
            projection
            for variant in executed
            for projection in blob_projections(variant.input_payload)
        ),
        "provider_identity": frozenset(
            value
            for identity in (
                "hpvbkq7319zxlmctvraozepdu",
                "hpvedq8427zkxwmscnufajory",
            )
            for value in text_projections(identity)
        ),
        "resource_identity": frozenset(
            projection
            for seed in resource_seeds
            for projection in text_projections(seed, minimum_chunk_length=12)
        ),
    }
    if tuple(corpus) != FORBIDDEN_SOURCE_CLASSES:
        raise PrivacyEvidenceError("C1 forbidden source classes are incomplete")
    allowed_output = (
        repo_root / FIXTURE_DIRECTORY / "c1-inert-report-example-output.json"
    ).read_text(encoding="utf-8")
    filtered = {
        source_class: frozenset(
            projection
            for projection in projections
            if projection and projection not in allowed_output
        )
        for source_class, projections in corpus.items()
    }
    if any(not projections for projections in filtered.values()):
        raise PrivacyEvidenceError("C1 forbidden source class has no projections")
    return filtered


def assert_channels_private(
    channels: Mapping[str, list[str]], corpus: Mapping[str, frozenset[str]]
) -> None:
    assert_projection_channels_private(
        channels,
        corpus,
        expected_channels=CAPTURE_CHANNELS,
        expected_source_classes=FORBIDDEN_SOURCE_CLASSES,
    )


def assert_projection_channels_private(
    channels: Mapping[str, list[str]],
    corpus: Mapping[str, frozenset[str]],
    *,
    expected_channels: tuple[str, ...],
    expected_source_classes: tuple[str, ...],
) -> None:
    if set(channels) != set(expected_channels):
        raise PrivacyEvidenceError("privacy capture channels are incomplete")
    if set(corpus) != set(expected_source_classes):
        raise PrivacyEvidenceError("privacy source classes are incomplete")
    for channel, values in channels.items():
        rendered = "\n".join(values)
        for source_class, projections in corpus.items():
            for projection in projections:
                if projection and projection in rendered:
                    raise PrivacyEvidenceError(
                        f"{source_class} projection reached {channel}"
                    )


def _verify_source_documents(repo_root: Path) -> None:
    config_contract = _decode_json(
        (repo_root / "docs/reference/hardcover-config-v1.json").read_bytes()
    )
    hardcover_provider = config_contract.get("hardcover_provider")
    if (
        config_contract.get("version") != "hardcover-config-v1"
        or not isinstance(hardcover_provider, Mapping)
        or hardcover_provider.get("additional_properties") is not False
        or hardcover_provider.get("present_required")
        != ["enabled", "secret_reference", "authority_revision"]
    ):
        raise PrivacyEvidenceError("C1 configuration contract is malformed")
    query_schema_path = (
        repo_root / "docs/reference/hardcover-private-query-v1.schema.json"
    )
    query_schema_payload = query_schema_path.read_bytes()
    query_schema = _decode_json(query_schema_payload)
    if query_schema != {
        "additionalProperties": False,
        "properties": {
            "isbn": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": ["isbn", "title"],
        "type": "object",
    }:
        raise PrivacyEvidenceError("C1 private-query schema is malformed")
    query_contract = _decode_json(
        (repo_root / "docs/reference/hardcover-private-query-v1.json").read_bytes()
    )
    decode = query_contract.get("decode")
    file_contract = query_contract.get("file")
    if (
        query_contract.get("version") != "hardcover-private-query-v1"
        or not isinstance(decode, Mapping)
        or decode.get("schema_bytes") != len(query_schema_payload)
        or decode.get("schema_sha256")
        != hashlib.sha256(query_schema_payload).hexdigest()
        or not isinstance(file_contract, Mapping)
        or file_contract.get("stable_fstat")
        != ["device", "inode", "mode", "uid", "nlink", "size"]
    ):
        raise PrivacyEvidenceError("C1 private-query contract is malformed")
    schema = _load_canonical(repo_root / OUTPUT_SCHEMA_PATH)
    if schema != output_schema_document():
        raise PrivacyEvidenceError("C1 output schema is not the generated schema")
    contract = _load_canonical(repo_root / PRIVACY_CONTRACT_PATH)
    if contract != privacy_contract_document():
        raise PrivacyEvidenceError("C1 privacy contract is not generated")
    output = _load_canonical(
        repo_root / FIXTURE_DIRECTORY / "c1-inert-report-example-output.json"
    )
    validate_output_document(output)
    input_schema = _load_canonical(
        repo_root / FIXTURE_DIRECTORY / "c1-inert-report-input-schema.json"
    )
    if input_schema.get("additionalProperties") is not False:
        raise PrivacyEvidenceError("C1 input schema is not closed")
    input_document = _load_canonical(
        repo_root / FIXTURE_DIRECTORY / "c1-inert-report-input.json"
    )
    if set(input_document) != {"schema", "scenario", "config", "private_query"}:
        raise PrivacyEvidenceError("C1 input document is not exact")
    matrix = _load_canonical(
        repo_root / FIXTURE_DIRECTORY / "c1-inert-report-variant-matrix.json"
    )
    if matrix.get("scenario") != "inert-report-v3" or not matrix.get("cases"):
        raise PrivacyEvidenceError("C1 variant matrix is malformed")
    if any(
        path.is_absolute()
        for path in (*RESOURCE_PATHS, PRIVACY_SCAN_PATH, REGISTRY_PATH)
    ):
        raise PrivacyEvidenceError("C1 resource path is not repository-relative")


def _load_canonical(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    source = _decode_json(payload)
    if canonical_bytes(source) != payload:
        raise PrivacyEvidenceError("C1 artifact is not canonical JSON")
    return source


def _decode_json(payload: bytes) -> dict[str, object]:
    try:
        text = payload.decode("utf-8", "strict")
        source = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise PrivacyEvidenceError("C1 artifact is invalid JSON") from None
    if not isinstance(source, dict):
        raise PrivacyEvidenceError("C1 artifact must be an object")
    return source


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PrivacyEvidenceError("C1 artifact contains duplicate keys")
        result[key] = value
    return result


def text_projections(
    value: str,
    *,
    allow_numeric_prefix: bool = False,
    minimum_chunk_length: int = 8,
) -> frozenset[str]:
    encoded = value.encode("utf-8")
    textual = {
        value,
        value.casefold(),
        value.upper(),
        normalized_title_key(value),
        unicodedata.normalize("NFC", value),
        unicodedata.normalize("NFKC", value),
        json.dumps(value, ensure_ascii=True),
        repr(value),
        encoded.hex(),
        base64.b16encode(encoded).decode("ascii"),
        base64.b32encode(encoded).decode("ascii"),
        base64.b64encode(encoded).decode("ascii"),
        base64.urlsafe_b64encode(encoded).decode("ascii"),
        quote(value),
        quote_plus(value),
        f"length:{len(value)}",
        f"utf8-length:{len(encoded)}",
        json.dumps({"length": len(value)}, separators=(",", ":")),
        json.dumps({"utf8_length": len(encoded)}, separators=(",", ":")),
    }
    tokens = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", value)
    chunks = {
        token
        for token in tokens
        if len(token) >= minimum_chunk_length and not token.isdigit()
    }
    for left, right in zip(tokens, tokens[1:], strict=False):
        combined = left + right
        if (
            minimum_chunk_length <= len(combined) <= 32
            and not combined.isdigit()
            and (
                len(left) >= minimum_chunk_length
                or len(right) >= minimum_chunk_length
                or (len(left) >= 4 and len(right) >= 4)
            )
        ):
            chunks.add(combined)
    chunks.update(
        chunk[:minimum_chunk_length]
        for chunk in tuple(chunks)
        if len(chunk) > minimum_chunk_length
    )
    if allow_numeric_prefix and value.isdigit() and len(value) >= 10:
        chunks.add(value[:8])
    expanded_chunks = {
        projection
        for chunk in chunks
        for projection in (chunk, chunk.casefold(), chunk.upper())
    }
    return frozenset(
        textual | expanded_chunks | digest_projections(encoded)
    )


def blob_projections(payload: bytes) -> frozenset[str]:
    return frozenset(
        {
            payload.decode("utf-8", "strict"),
            payload.hex(),
            base64.b16encode(payload).decode("ascii"),
            base64.b32encode(payload).decode("ascii"),
            base64.b64encode(payload).decode("ascii"),
            base64.urlsafe_b64encode(payload).decode("ascii"),
            *digest_projections(payload),
        }
    )


def digest_projections(payload: bytes) -> frozenset[str]:
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
    result: set[str] = set()
    for digester in digesters:
        raw = digester.digest()
        hexadecimal = digester.hexdigest()
        result.update(
            {
                hexadecimal,
                hexadecimal[:8],
                base64.b64encode(raw).decode("ascii"),
                base64.urlsafe_b64encode(raw).decode("ascii"),
            }
        )
    return frozenset(result)


def _reject_future_markers(payload: bytes) -> None:
    folded = payload.decode("utf-8").casefold()
    if any(marker in folded for marker in _FUTURE_MARKERS):
        raise PrivacyEvidenceError("C1 privacy evidence contains a future marker")


__all__ = [
    "ALTERNATE_ISBN",
    "ALLOWED_RESULT_KEYS",
    "CAPTURE_CHANNELS",
    "C1InputVariant",
    "FORBIDDEN_RESULT_KEY_FRAGMENTS",
    "PRIVACY_CONTRACT_PATH",
    "PRIVACY_SCAN_PATH",
    "REGISTRY_PATH",
    "PrivacyEvidenceError",
    "TITLE_ONLY_ALTERNATE",
    "assert_channels_private",
    "assert_projection_channels_private",
    "blob_projections",
    "build_registry",
    "build_scan",
    "canonical_bytes",
    "c1_input_variants",
    "digest_projections",
    "forbidden_source_corpus",
    "output_schema_document",
    "privacy_contract_document",
    "text_projections",
    "validate_output_document",
]
