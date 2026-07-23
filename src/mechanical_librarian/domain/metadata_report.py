"""Closed, neutral metadata-evidence report for one selected EPUB."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Self


REPORT_SCHEMA = "metadata-report-v1"
MAX_REPORT_BYTES = 262_144
MAX_CANDIDATES = 4
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ID = re.compile(r"^report_[0-9a-f]{64}$")
_REFERENCE = re.compile(r"^(?:local|provider)_[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ReportValueError(ValueError):
    """A report or report input is outside the closed 0.4 contract."""


class ClaimKind(StrEnum):
    TITLE = "title"
    ISBN = "isbn"


class ProviderStatus(StrEnum):
    DISABLED = "disabled"
    INSUFFICIENT_INPUT = "insufficient_input"
    CANDIDATES = "candidates"
    NO_MATCH = "no_match"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"


class Comparison(StrEnum):
    AGREEMENT = "agreement"
    DISAGREEMENT = "disagreement"
    INCOMPATIBLE = "incompatible"
    NOT_COMPARABLE = "not_comparable"


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, RecursionError) as error:
        raise ReportValueError("report value is not serializable") from error


def stable_reference(prefix: str, value: object) -> str:
    if prefix not in {"local", "provider"}:
        raise ReportValueError("claim reference prefix is invalid")
    return f"{prefix}_{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


def normalize_title(value: str) -> str:
    if type(value) is not str:
        raise ReportValueError("title is invalid")
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    _text(normalized, "title", 512)
    return normalized


@dataclass(frozen=True, slots=True)
class TargetFacts:
    connection: str
    library: str
    item: str
    current_title: str

    def __post_init__(self) -> None:
        _name(self.connection, "connection")
        _name(self.library, "library")
        _text(self.item, "item", 512)
        _text(self.current_title, "current title", 512)

    def to_data(self) -> dict[str, str]:
        return {
            "connection": self.connection,
            "current_title": self.current_title,
            "item": self.item,
            "library": self.library,
        }


@dataclass(frozen=True, slots=True)
class FileFacts:
    selected_index: int
    identity: str
    content_digest: str
    format: str = "epub"

    def __post_init__(self) -> None:
        if type(self.selected_index) is not int or not 0 <= self.selected_index <= 10_000:
            raise ReportValueError("selected file index is invalid")
        if _DIGEST.fullmatch(self.identity) is None or _DIGEST.fullmatch(self.content_digest) is None:
            raise ReportValueError("selected file identity is invalid")
        if self.format != "epub":
            raise ReportValueError("selected file format is unsupported")

    def to_data(self) -> dict[str, object]:
        return {
            "content_digest": self.content_digest,
            "format": self.format,
            "identity": self.identity,
            "selected_index": self.selected_index,
        }


@dataclass(frozen=True, slots=True)
class LocalClaim:
    reference: str
    kind: ClaimKind
    observed: str
    canonical: str | None
    excerpt: str
    applicability: str
    basis: str
    checksum: str
    applicability_basis: str

    def __post_init__(self) -> None:
        if _REFERENCE.fullmatch(self.reference) is None or not self.reference.startswith("local_"):
            raise ReportValueError("local claim reference is invalid")
        if type(self.kind) is not ClaimKind:
            raise ReportValueError("local claim kind is invalid")
        _text(self.observed, "observed claim", 280)
        if self.canonical is not None:
            _text(self.canonical, "canonical claim", 512)
        _text(self.excerpt, "claim excerpt", 280)
        if self.observed not in self.excerpt:
            raise ReportValueError("claim excerpt does not support its observation")
        if self.applicability not in {"ebook", "print", "unknown", "ambiguous", "not_applicable"}:
            raise ReportValueError("claim applicability is invalid")
        if self.basis not in {"opening_matter", "target_metadata"}:
            raise ReportValueError("claim basis is invalid")
        if self.checksum not in {"valid_isbn_10", "valid_isbn_13", "invalid", "not_applicable"}:
            raise ReportValueError("claim checksum is invalid")
        if self.applicability_basis not in {"ebook", "paperback", "hardcover", "print", "unlabelled", "ambiguous", "not_applicable"}:
            raise ReportValueError("claim applicability basis is invalid")
        if self.kind is ClaimKind.TITLE and (
            self.checksum != "not_applicable"
            or self.applicability != "not_applicable"
            or self.applicability_basis != "not_applicable"
        ):
            raise ReportValueError("title claim has identifier fields")
        if self.kind is ClaimKind.ISBN and (
            self.checksum.startswith("valid_") != (self.canonical is not None)
            or self.applicability == "not_applicable"
            or self.applicability_basis == "not_applicable"
        ):
            raise ReportValueError("ISBN claim typing is inconsistent")
        if self.kind is ClaimKind.ISBN and self.canonical is not None and not _valid_isbn(self.canonical, self.checksum):
            raise ReportValueError("ISBN claim checksum is inconsistent")
        if self.kind is ClaimKind.TITLE and self.canonical != normalize_title(self.observed):
            raise ReportValueError("title claim normalization is inconsistent")
        expected = stable_reference("local", self._reference_data())
        if self.reference != expected:
            raise ReportValueError("local claim reference is inconsistent")

    @classmethod
    def create(
        cls,
        *,
        kind: ClaimKind,
        observed: str,
        canonical: str | None,
        excerpt: str,
        applicability: str,
        basis: str = "opening_matter",
        checksum: str,
        applicability_basis: str,
    ) -> Self:
        value = {
            "applicability": applicability,
            "basis": basis,
            "canonical": canonical,
            "checksum": checksum,
            "excerpt": excerpt,
            "kind": kind.value,
            "observed": observed,
            "applicability_basis": applicability_basis,
        }
        return cls(stable_reference("local", value), kind, observed, canonical, excerpt, applicability, basis, checksum, applicability_basis)

    def _reference_data(self) -> dict[str, object]:
        return {
            "applicability": self.applicability,
            "basis": self.basis,
            "canonical": self.canonical,
            "checksum": self.checksum,
            "excerpt": self.excerpt,
            "kind": self.kind.value,
            "observed": self.observed,
            "applicability_basis": self.applicability_basis,
        }

    def to_data(self) -> dict[str, object]:
        return {"reference": self.reference, **self._reference_data()}


@dataclass(frozen=True, slots=True)
class ProviderClaim:
    reference: str
    kind: ClaimKind
    value: str
    comparison: Comparison
    local_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if _REFERENCE.fullmatch(self.reference) is None or not self.reference.startswith("provider_"):
            raise ReportValueError("provider claim reference is invalid")
        if type(self.kind) is not ClaimKind or type(self.comparison) is not Comparison:
            raise ReportValueError("provider claim is invalid")
        _text(self.value, "provider claim value", 512)
        if self.kind is ClaimKind.ISBN and not (
            _valid_isbn(self.value, "valid_isbn_10")
            or _valid_isbn(self.value, "valid_isbn_13")
        ):
            raise ReportValueError("provider ISBN claim is invalid")
        if type(self.local_references) is not tuple or any(
            _REFERENCE.fullmatch(value) is None or not value.startswith("local_")
            for value in self.local_references
        ) or tuple(sorted(set(self.local_references))) != self.local_references:
            raise ReportValueError("provider claim references are invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "comparison": self.comparison.value,
            "kind": self.kind.value,
            "local_references": list(self.local_references),
            "reference": self.reference,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    identity: str
    claims: tuple[ProviderClaim, ...]

    def __post_init__(self) -> None:
        _text(self.identity, "provider candidate identity", 128)
        if type(self.claims) is not tuple or any(type(value) is not ProviderClaim for value in self.claims):
            raise ReportValueError("provider candidate claims are invalid")
        references = tuple(value.reference for value in self.claims)
        if len(set(references)) != len(references):
            raise ReportValueError("provider candidate claim references are duplicated")
        if any(
            value.reference != stable_reference(
                "provider",
                {"candidate": self.identity, "kind": value.kind.value, "value": value.value},
            )
            for value in self.claims
        ):
            raise ReportValueError("provider candidate claim reference is inconsistent")

    def to_data(self) -> dict[str, object]:
        return {"claims": [value.to_data() for value in self.claims], "identity": self.identity}


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    provider: str
    status: ProviderStatus
    input_references: tuple[str, ...] = ()
    candidates: tuple[ProviderCandidate, ...] = ()

    def __post_init__(self) -> None:
        _name(self.provider, "provider")
        if type(self.status) is not ProviderStatus:
            raise ReportValueError("provider status is invalid")
        if type(self.input_references) is not tuple or tuple(sorted(set(self.input_references))) != self.input_references or any(
            _REFERENCE.fullmatch(value) is None or not value.startswith("local_")
            for value in self.input_references
        ):
            raise ReportValueError("provider input references are invalid")
        if type(self.candidates) is not tuple or len(self.candidates) > MAX_CANDIDATES or any(type(value) is not ProviderCandidate for value in self.candidates):
            raise ReportValueError("provider candidates are invalid")
        identities = tuple(value.identity for value in self.candidates)
        if identities != tuple(sorted(set(identities))):
            raise ReportValueError("provider candidates are not in canonical identity order")
        if (self.status is ProviderStatus.CANDIDATES) != bool(self.candidates):
            raise ReportValueError("provider status and candidates disagree")
        if self.status in {ProviderStatus.DISABLED, ProviderStatus.INSUFFICIENT_INPUT} and self.input_references:
            raise ReportValueError("inactive provider cannot claim inputs")

    def to_data(self) -> dict[str, object]:
        return {
            "candidates": [value.to_data() for value in self.candidates],
            "input_references": list(self.input_references),
            "provider": self.provider,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class MetadataReport:
    report_id: str
    target: TargetFacts
    file: FileFacts
    local_claims: tuple[LocalClaim, ...]
    local_status: str
    local_reasons: tuple[str, ...]
    providers: tuple[ProviderObservation, ...]
    schema_version: str = REPORT_SCHEMA

    def __post_init__(self) -> None:
        if _REPORT_ID.fullmatch(self.report_id) is None or type(self.target) is not TargetFacts or type(self.file) is not FileFacts:
            raise ReportValueError("report identity or target is invalid")
        if self.schema_version != REPORT_SCHEMA:
            raise ReportValueError("report schema is unsupported")
        if type(self.local_claims) is not tuple or any(type(value) is not LocalClaim for value in self.local_claims):
            raise ReportValueError("local claims are invalid")
        references = tuple(value.reference for value in self.local_claims)
        if len(set(references)) != len(references):
            raise ReportValueError("local claim references are duplicated")
        has_opening_candidate = any(value.basis == "opening_matter" for value in self.local_claims)
        if self.local_status not in {"candidate", "no_candidate"} or (self.local_status == "candidate") != has_opening_candidate:
            raise ReportValueError("local candidate status is invalid")
        if type(self.local_reasons) is not tuple or not self.local_reasons or tuple(sorted(set(self.local_reasons))) != self.local_reasons:
            raise ReportValueError("local reasons are invalid")
        for value in self.local_reasons:
            _name(value, "local reason")
        if type(self.providers) is not tuple or any(type(value) is not ProviderObservation for value in self.providers):
            raise ReportValueError("provider observations are invalid")
        names = tuple(value.provider for value in self.providers)
        if names != tuple(sorted(set(names))):
            raise ReportValueError("provider observations are not in canonical order")
        known = set(references)
        if any(not set(provider.input_references).issubset(known) for provider in self.providers):
            raise ReportValueError("provider inputs do not reference local claims")
        provider_claims = tuple(
            claim
            for provider in self.providers
            for candidate in provider.candidates
            for claim in candidate.claims
        )
        if len({claim.reference for claim in provider_claims}) != len(provider_claims) or any(
            not set(claim.local_references).issubset(known) for claim in provider_claims
        ):
            raise ReportValueError("provider candidate claim references are invalid")
        expected = f"report_{hashlib.sha256(canonical_json(self._identity_data()).encode('utf-8')).hexdigest()}"
        if self.report_id != expected:
            raise ReportValueError("report identity is inconsistent")
        if len(self.to_json().encode("utf-8")) > MAX_REPORT_BYTES:
            raise ReportValueError("report exceeds its byte limit")

    @classmethod
    def create(
        cls,
        *,
        target: TargetFacts,
        file: FileFacts,
        local_claims: tuple[LocalClaim, ...],
        local_reasons: tuple[str, ...],
        providers: tuple[ProviderObservation, ...],
    ) -> Self:
        local_status = "candidate" if any(value.basis == "opening_matter" for value in local_claims) else "no_candidate"
        data = _report_data("", target, file, local_claims, local_status, local_reasons, providers)
        data.pop("report_id")
        identity = f"report_{hashlib.sha256(canonical_json(data).encode('utf-8')).hexdigest()}"
        return cls(identity, target, file, local_claims, local_status, local_reasons, providers)

    def _identity_data(self) -> dict[str, object]:
        value = self.to_data()
        value.pop("report_id")
        return value

    def to_data(self) -> dict[str, object]:
        return _report_data(self.report_id, self.target, self.file, self.local_claims, self.local_status, self.local_reasons, self.providers)

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        if type(serialized) is not str or not serialized or len(serialized.encode("utf-8")) > MAX_REPORT_BYTES:
            raise ReportValueError("report document is invalid")
        try:
            source = json.loads(
                serialized,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
            )
            report = _report_from_data(source)
        except ReportValueError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, json.JSONDecodeError) as error:
            raise ReportValueError("report document is invalid") from error
        if report.to_json() != serialized:
            raise ReportValueError("report document is not canonical")
        return report


def compare_claim(kind: ClaimKind, value: str, local: tuple[LocalClaim, ...]) -> tuple[Comparison, tuple[str, ...]]:
    relevant = tuple(item for item in local if item.kind is kind and item.canonical is not None)
    references = tuple(sorted(item.reference for item in relevant))
    if not relevant:
        return Comparison.NOT_COMPARABLE, ()
    if kind is ClaimKind.TITLE:
        normalized = normalize_title(value).casefold()
        local_values = {normalize_title(item.canonical or "").casefold() for item in relevant}
        return (Comparison.AGREEMENT if normalized in local_values else Comparison.DISAGREEMENT), references
    digits = "".join(character for character in value if character.isdigit() or character in "Xx").upper()
    local_values = {item.canonical for item in relevant}
    return (Comparison.AGREEMENT if digits in local_values else Comparison.INCOMPATIBLE), references


def _report_data(report_id: str, target: TargetFacts, file: FileFacts, local_claims: tuple[LocalClaim, ...], local_status: str, local_reasons: tuple[str, ...], providers: tuple[ProviderObservation, ...]) -> dict[str, object]:
    return {
        "file": file.to_data(),
        "local": {
            "claims": [value.to_data() for value in local_claims],
            "reasons": list(local_reasons),
            "status": local_status,
        },
        "providers": [value.to_data() for value in providers],
        "report_id": report_id,
        "schema_version": REPORT_SCHEMA,
        "target": target.to_data(),
    }


def _report_from_data(value: object) -> MetadataReport:
    root = _object(value, {"file", "local", "providers", "report_id", "schema_version", "target"})
    target = _object(root["target"], {"connection", "current_title", "item", "library"})
    file = _object(root["file"], {"content_digest", "format", "identity", "selected_index"})
    local = _object(root["local"], {"claims", "reasons", "status"})
    claims = tuple(_local_claim_from_data(item) for item in _list(local["claims"]))
    providers = tuple(_provider_from_data(item) for item in _list(root["providers"]))
    reasons = tuple(_strings(local["reasons"]))
    return MetadataReport(
        root["report_id"],
        TargetFacts(target["connection"], target["library"], target["item"], target["current_title"]),
        FileFacts(file["selected_index"], file["identity"], file["content_digest"], file["format"]),
        claims,
        local["status"],
        reasons,
        providers,
        root["schema_version"],
    )


def _local_claim_from_data(value: object) -> LocalClaim:
    item = _object(value, {"applicability", "applicability_basis", "basis", "canonical", "checksum", "excerpt", "kind", "observed", "reference"})
    return LocalClaim(item["reference"], ClaimKind(item["kind"]), item["observed"], item["canonical"], item["excerpt"], item["applicability"], item["basis"], item["checksum"], item["applicability_basis"])


def _provider_from_data(value: object) -> ProviderObservation:
    item = _object(value, {"candidates", "input_references", "provider", "status"})
    candidates = tuple(_candidate_from_data(candidate) for candidate in _list(item["candidates"]))
    return ProviderObservation(item["provider"], ProviderStatus(item["status"]), tuple(_strings(item["input_references"])), candidates)


def _candidate_from_data(value: object) -> ProviderCandidate:
    item = _object(value, {"claims", "identity"})
    return ProviderCandidate(item["identity"], tuple(_provider_claim_from_data(claim) for claim in _list(item["claims"])))


def _provider_claim_from_data(value: object) -> ProviderClaim:
    item = _object(value, {"comparison", "kind", "local_references", "reference", "value"})
    return ProviderClaim(item["reference"], ClaimKind(item["kind"]), item["value"], Comparison(item["comparison"]), tuple(_strings(item["local_references"])))


def _object(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ReportValueError("report object is malformed")
    return value


def _list(value: object) -> list[Any]:
    if type(value) is not list:
        raise ReportValueError("report list is malformed")
    return value


def _strings(value: object) -> list[str]:
    result = _list(value)
    if any(type(item) is not str for item in result):
        raise ReportValueError("report string list is malformed")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReportValueError("report object contains a duplicate field")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ReportValueError("report number is invalid")


def _name(value: object, label: str) -> None:
    if type(value) is not str or _NAME.fullmatch(value) is None:
        raise ReportValueError(f"{label} is invalid")


def _text(value: object, label: str, maximum: int) -> None:
    if type(value) is not str or not value or len(value) > maximum or value.strip() != value or "\x00" in value or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise ReportValueError(f"{label} is invalid")


def _valid_isbn(value: str, checksum: str) -> bool:
    if checksum == "valid_isbn_13" and len(value) == 13 and value.isascii() and value.isdigit():
        expected = (10 - sum((1 if index % 2 == 0 else 3) * int(character) for index, character in enumerate(value[:12])) % 10) % 10
        return int(value[-1]) == expected
    if checksum == "valid_isbn_10" and len(value) == 10 and value[:9].isascii() and value[:9].isdigit() and (value[-1].isascii() and value[-1].isdigit() or value[-1] == "X"):
        return sum((10 - index) * (10 if character == "X" else int(character)) for index, character in enumerate(value)) % 11 == 0
    return False


__all__ = [
    "ClaimKind", "Comparison", "FileFacts", "LocalClaim", "MAX_CANDIDATES",
    "MAX_REPORT_BYTES", "MetadataReport", "ProviderCandidate", "ProviderClaim",
    "ProviderObservation", "ProviderStatus", "REPORT_SCHEMA", "ReportValueError",
    "TargetFacts", "canonical_json", "compare_claim", "normalize_title", "stable_reference",
]
