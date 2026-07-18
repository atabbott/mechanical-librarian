"""Executable C1-only public privacy evidence for the inert Hardcover report."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote, quote_plus

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


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def output_schema_document() -> dict[str, object]:
    return {
        "$id": "hardcover-c1-inert-report-output-v1",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "result": {
                "additionalProperties": False,
                "properties": {
                    "database_opened": {"type": "boolean"},
                    "network_started": {"type": "boolean"},
                    "outcome": {"enum": ["disabled", "invalid_input"]},
                    "private_file_opened": {"type": "boolean"},
                    "secret_opened": {"type": "boolean"},
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
        if type(result.get(key)) is not bool:
            raise PrivacyEvidenceError("C1 access counter is not a strict boolean")


def forbidden_source_corpus(
    repo_root: Path, *, additional_titles: tuple[str, ...] = ()
) -> dict[str, frozenset[str]]:
    source_payload = (
        repo_root / FIXTURE_DIRECTORY / "c1-inert-report-input.json"
    ).read_bytes()
    source = _decode_json(source_payload)
    private_query = source["private_query"]
    title = private_query["title"]
    isbn = private_query["isbn"]
    if type(title) is not str or type(isbn) is not str:
        raise PrivacyEvidenceError("C1 canonical input is malformed")
    query_payload = canonical_bytes(private_query).rstrip(b"\n")
    resource_values: set[str] = set()
    for path in (*RESOURCE_PATHS, PRIVACY_SCAN_PATH, REGISTRY_PATH):
        payload = (repo_root / path).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        resource_values.update(
            {
                path.as_posix(),
                path.name,
                digest,
                digest[:8],
                f"sha256:{digest}",
            }
        )
    corpus = {
        "title": frozenset(
            projection
            for candidate in (title, *additional_titles)
            for projection in _text_projections(candidate)
        ),
        "canonical_isbn": _text_projections(isbn),
        "serialized_private_query": _blob_projections(query_payload),
        "serialized_input": _blob_projections(source_payload),
        "provider_identity": frozenset(
            value
            for identity in (
                "hpvbkq7319zxlmctvraozepdu",
                "hpvedq8427zkxwmscnufajory",
            )
            for value in _text_projections(identity)
        ),
        "resource_identity": frozenset(resource_values),
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
    if set(channels) != set(CAPTURE_CHANNELS):
        raise PrivacyEvidenceError("privacy capture channels are incomplete")
    if set(corpus) != set(FORBIDDEN_SOURCE_CLASSES):
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


def _text_projections(value: str) -> frozenset[str]:
    encoded = value.encode("utf-8")
    textual = {
        value,
        value.casefold(),
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
        str(len(value)),
        str(len(encoded)),
        f"scalar-count={len(value)}",
        f"utf8-bytes={len(encoded)}",
    }
    tokens = set(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", value))
    tokens.update(token.casefold() for token in tuple(tokens))
    fragments = {
        item[index : index + 8]
        for item in textual | tokens
        for index in range(0, len(item), 8)
        if len(item[index : index + 8]) >= 4
    }
    fragments.update(item[:8] for item in textual | tokens if len(item) >= 8)
    return frozenset(textual | tokens | fragments | _digest_projections(encoded))


def _blob_projections(payload: bytes) -> frozenset[str]:
    return frozenset(
        {
            payload.decode("utf-8", "strict"),
            payload.hex(),
            base64.b16encode(payload).decode("ascii"),
            base64.b32encode(payload).decode("ascii"),
            base64.b64encode(payload).decode("ascii"),
            base64.urlsafe_b64encode(payload).decode("ascii"),
            *_digest_projections(payload),
        }
    )


def _digest_projections(payload: bytes) -> set[str]:
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
    return result


def _reject_future_markers(payload: bytes) -> None:
    folded = payload.decode("utf-8").casefold()
    if any(marker in folded for marker in _FUTURE_MARKERS):
        raise PrivacyEvidenceError("C1 privacy evidence contains a future marker")


__all__ = [
    "ALLOWED_RESULT_KEYS",
    "CAPTURE_CHANNELS",
    "FORBIDDEN_RESULT_KEY_FRAGMENTS",
    "PRIVACY_CONTRACT_PATH",
    "PRIVACY_SCAN_PATH",
    "REGISTRY_PATH",
    "PrivacyEvidenceError",
    "assert_channels_private",
    "build_registry",
    "build_scan",
    "canonical_bytes",
    "forbidden_source_corpus",
    "output_schema_document",
    "privacy_contract_document",
    "validate_output_document",
]
