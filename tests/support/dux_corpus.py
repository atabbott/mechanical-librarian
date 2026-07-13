"""Strict, immutable loader for the generated DUX regression corpus."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    EvidenceId,
    ExternalItemId,
    LibraryId,
    ProposalId,
    ReviewCaseId,
    SnapshotId,
)


SCHEMA_VERSION = "dux-corpus-v1"
SOURCE_COMMIT = "358d70039a2d7da5e1d4cf7f19032ed5bf0fdc3f"
SOURCE_TAG = "dux-complete-2026-07-10"
CORPUS_ROOT = Path(__file__).parents[1] / "fixtures" / "dux"
REGISTRY_PATH = CORPUS_ROOT / "registry.json"
MAX_FILE_BYTES = 65_536
MAX_STRING_CHARS = 512

CATEGORIES = frozenset(
    {
        "repeated_candidates",
        "evidence_only_input",
        "consensus",
        "conflicts",
        "no_ops",
        "unloaded_current_values",
        "incompatible_fields",
        "cover_choices",
        "series_choices",
        "independent_siblings",
        "destructive_cases",
    }
)
CURRENT_CATEGORIES = frozenset(
    {"evidence_only_input", "no_ops", "independent_siblings", "destructive_cases"}
)

_FUTURE_CRITERIA = MappingProxyType(
    {
        70: frozenset(
            {
                "Distinguish fill-empty, replace-populated, exact no-op, normalized no-op, keep-current, unloaded-current, unsupported, and inapplicable behavior.",
                "Enforce adapter/media applicability before a proposal can become selectable.",
                "Represent series membership and sequence independently, including numeric, decimal, labelled, conflicting-order, and unknown values.",
            }
        ),
        71: frozenset(
            {
                "Produce typed cover choices and reasons without applying target changes.",
            }
        ),
        145: frozenset(
            {
                "Agreement is counted across independent evidence families, so correlated or repeated rows cannot manufacture consensus; policy-defined decisive single-family evidence remains possible.",
                "Proposal qualification requires supported target and work identity, plus edition identity or applicability when the field policy or proposed change is edition-sensitive; relevant conflicting identity or comparable identifiers reject the candidate.",
            }
        ),
    }
)

_SCENARIO_ID = re.compile(r"^dux-synthetic-[0-9]{2}$")
_SAFE_FILE = re.compile(r"^dux-synthetic-[0-9]{2}\.json$")
_CONTROL_OR_SURROGATE = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
_BIDI = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
# A preceding non-word, non-path-separator boundary covers ordinary prose and
# Markdown punctuation while leaving an embedded ``word/fragment`` untouched.
_PATH = re.compile(
    r"(?<![\w/\\])(?:~[/\\]|\$\{?home\}?[/\\]|/(?!/)(?:[^/\s]+/)*[^/\s]+|[A-Za-z]:[\\/]|\\\\)|"
    r"(?:^|[/\\])(?:workspace|private|tmp)(?:[/\\]|$)",
    re.IGNORECASE,
)
_URL_EMAIL_DOMAIN = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|www\.|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}\b)",
    re.IGNORECASE,
)
_GENERIC_URI_SCHEME = re.compile(
    r"(?<![\w+.-])[a-z][a-z0-9+.-]*:(?!\s)",
    re.IGNORECASE,
)
_IP = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b|(?:^|[^0-9a-f:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,4}", re.IGNORECASE)
_SECRET = re.compile(
    r"\b(?:authorization|bearer|cookie|credential|header|key|password|passwd|secret|api key|apikey|private key|privatekey|access key(?: id)?|accesskey(?:id)?|secret key|secretkey|access token|accesstoken|refresh token|refreshtoken|client secret|clientsecret)\b",
    re.IGNORECASE,
)
_STATE = re.compile(
    r"\b(?:database|report|runtime|production|saved browser state|support bundle)\b",
    re.IGNORECASE,
)
_PRODUCTION_ID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b|"
    r"\b(?:97[89][ -]?)?[0-9][0-9 -]{8,15}[0-9Xx]\b",
    re.IGNORECASE,
)
_PROTOTYPE = re.compile(
    r"(?:<html|\blocalstorage\b|\bsessionstorage\b|\bdashboard\b|\bgenerated source marker\b|\bprototype\b|\brenderer\b|\bbrowser local\b)",
    re.IGNORECASE,
)
_COPYRIGHT = re.compile(
    r"(?:©|\(c\)|\bcopyright\b|\ball rights reserved\b|\bspdx filecopyrighttext\b)",
    re.IGNORECASE,
)
_RESERVED_VALUE = re.compile(r"^(?:dux-synthetic|synthetic)-[a-z0-9]+(?:-[a-z0-9]+)*$")
_DECIMAL_SEQUENCE = re.compile(r"^[1-9][0-9]{0,2}\.[0-9]{1,3}$")
_FUTURE_LABEL_MAX_CHARS = 128
_SCENARIO_KEYS = frozenset(
    {
        "schema",
        "scenario_id",
        "category",
        "dux_behavior",
        "preserved_invariant",
        "status",
        "not_yet_proving_product_behavior",
        "future_owner",
        "provenance",
        "licensing",
        "contract",
    }
)
_CONTRACT_SHAPES = MappingProxyType(
    {
        "repeated_candidates": ("future_contract_fact", {"candidate_values": list, "evidence_families": list}, {"agreement_families": int, "outcome": str}),
        "evidence_only_input": ("review_case_empty_rejection", {"case_id": str, "evidence_id": str, "proposals": list, "snapshot_id": str, "target": dict}, {"exception": str, "outcome": str}),
        "consensus": ("future_contract_fact", {"candidate_values": list, "evidence_families": list}, {"agreement_families": int, "outcome": str}),
        "conflicts": ("future_contract_fact", {"candidate_identity": str, "current_identity": str, "field": str}, {"outcome": str}),
        "no_ops": ("field_proposal_no_change", {"current": list, "evidence_id": str, "field": str, "proposal_id": str, "proposed": list, "snapshot_id": str, "target": dict}, {"current_equals_proposed": bool, "kind": str}),
        "unloaded_current_values": ("future_contract_fact", {"current_state": str, "field": str, "proposed": list}, {"outcome": str}),
        "incompatible_fields": ("future_contract_fact", {"field": str, "media_type": str, "proposed": list}, {"outcome": str}),
        "cover_choices": ("future_contract_fact", {"candidate": str, "current": str, "work_identity": str}, {"outcome": str}),
        "series_choices": ("future_contract_fact", {"raw_membership": list, "raw_sequence": list}, {"membership": list, "outcome": str, "sequence": list}),
        "independent_siblings": ("review_group_projection", {"confidence": str, "label": str, "link_method": str, "siblings": list}, {"decision_state_present": bool, "siblings": list}),
        "destructive_cases": ("ordinary_review_case_destructive_rejection", {"case_id": str, "destructive": bool, "proposal_id": str, "snapshot_id": str, "target": dict}, {"exception": str, "outcome": str}),
    }
)


class CorpusValidationError(ValueError):
    """A stable diagnostic that never includes rejected fixture values."""

    def __init__(self, rule: str, *, scenario: str | None = None, ordinal: int | None = None):
        self.rule = rule
        self.scenario = scenario if scenario and _SCENARIO_ID.fullmatch(scenario) else None
        self.ordinal = ordinal
        location = f" scenario={self.scenario}" if self.scenario else f" ordinal={ordinal or 0}"
        super().__init__(f"{rule}{location}")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    category: str
    status: str
    contract: Mapping[str, Any]
    record: Mapping[str, Any]
    canonical_bytes: bytes


@dataclass(frozen=True)
class CorpusRegistry:
    scenarios: tuple[Scenario, ...]
    by_id: Mapping[str, Scenario]
    coverage: Mapping[str, str]
    canonical_bytes: bytes


def canonical_json(value: Any) -> bytes:
    """Serialize the JSON data contract deterministically with one final LF."""

    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _fail(rule: str, *, scenario: str | None = None, ordinal: int | None = None) -> None:
    raise CorpusValidationError(rule, scenario=scenario, ordinal=ordinal)


def _read_json(path: Path, *, ordinal: int) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("DUX001_FILE_READ", ordinal=ordinal)
    if len(raw) > MAX_FILE_BYTES or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _fail("DUX002_FILE_BOUNDS", ordinal=ordinal)
    duplicate = False

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                duplicate = True
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError):
        _fail("DUX003_JSON", ordinal=ordinal)
    if duplicate or type(value) is not dict:
        _fail("DUX004_JSON_SHAPE", ordinal=ordinal)
    try:
        serialized = canonical_json(value)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("DUX020_UNSAFE_TEXT", ordinal=ordinal)
    if serialized != raw:
        _fail("DUX005_NONCANONICAL", ordinal=ordinal)
    return value, raw


def _exact_keys(value: object, keys: frozenset[str], rule: str, *, scenario: str | None, ordinal: int) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != keys:
        _fail(rule, scenario=scenario, ordinal=ordinal)
    return value


def _normalized_labels(value: str) -> str:
    decoded_delimiters = re.sub(
        r"%(?:0[9a-d]|20|2d|2e|3a|5f|2f)", " ", value, flags=re.IGNORECASE
    )
    return re.sub(r"[_.\-\s:/]+", " ", decoded_delimiters)


def _audit(value: Any, *, scenario: str, ordinal: int, path: tuple[str, ...] = ()) -> None:
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                _fail("DUX020_UNSAFE_TEXT", scenario=scenario, ordinal=ordinal)
            if _SECRET.search(_normalized_labels(key)):
                _fail("DUX021_SECRET_SHAPE", scenario=scenario, ordinal=ordinal)
            _audit(child, scenario=scenario, ordinal=ordinal, path=(*path, key))
        return
    if type(value) is list:
        for child in value:
            _audit(child, scenario=scenario, ordinal=ordinal, path=path)
        return
    if type(value) is not str:
        return
    if len(value) > MAX_STRING_CHARS or _CONTROL_OR_SURROGATE.search(value) or _BIDI.search(value):
        _fail("DUX020_UNSAFE_TEXT", scenario=scenario, ordinal=ordinal)
    if path == ("provenance", "source_commit") and value == SOURCE_COMMIT:
        return
    if path == ("provenance", "source_tag") and value == SOURCE_TAG:
        return
    if SOURCE_COMMIT in value or SOURCE_TAG in value:
        _fail("DUX027_SOURCE_LITERAL", scenario=scenario, ordinal=ordinal)
    normalized = _normalized_labels(value)
    checks = (
        ("DUX022_PROHIBITED_PATH", _PATH),
        ("DUX021_SECRET_SHAPE", _SECRET, normalized),
        ("DUX025_PRODUCTION_STATE", _STATE, normalized),
        ("DUX028_COPYRIGHT", _COPYRIGHT, normalized),
        ("DUX023_DOMAIN_OR_URL", _URL_EMAIL_DOMAIN),
        ("DUX024_NETWORK_ADDRESS", _IP),
        ("DUX026_PRODUCTION_ID", _PRODUCTION_ID),
        ("DUX027_SOURCE_LITERAL", _PROTOTYPE, normalized),
    )
    for item in checks:
        rule, pattern, *selected = item
        if pattern.search(selected[0] if selected else value):
            _fail(rule, scenario=scenario, ordinal=ordinal)
    if _GENERIC_URI_SCHEME.search(value):
        _fail("DUX023_DOMAIN_OR_URL", scenario=scenario, ordinal=ordinal)


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


def _validate_contract_shape(category: str, contract: dict[str, Any], *, scenario: str, ordinal: int) -> None:
    kind, input_shape, expected_shape = _CONTRACT_SHAPES[category]
    if contract["kind"] != kind:
        _fail("DUX016_CONTRACT", scenario=scenario, ordinal=ordinal)
    for value, shape in ((contract["input"], input_shape), (contract["expected"], expected_shape)):
        if frozenset(value) != frozenset(shape):
            _fail("DUX016_CONTRACT", scenario=scenario, ordinal=ordinal)
        for key, expected_type in shape.items():
            child = value[key]
            if type(child) is not expected_type:
                _fail("DUX016_CONTRACT", scenario=scenario, ordinal=ordinal)
            if expected_type is list and any(type(item) is not str for item in child):
                _fail("DUX016_CONTRACT", scenario=scenario, ordinal=ordinal)
            if expected_type is str and not child:
                _fail("DUX016_CONTRACT", scenario=scenario, ordinal=ordinal)
            if expected_type is dict and (
                frozenset(child) != frozenset({"adapter", "connection", "external_item", "library"})
                or any(type(item) is not str or not item for item in child.values())
            ):
                _fail("DUX016_CONTRACT", scenario=scenario, ordinal=ordinal)

    inputs = contract["input"]
    expected = contract["expected"]

    def require(condition: bool) -> None:
        if not condition:
            _fail("DUX016_CONTRACT", scenario=scenario, ordinal=ordinal)

    def synthetic_label(value: object) -> bool:
        return (
            type(value) is str
            and len(value) <= _FUTURE_LABEL_MAX_CHARS
            and _RESERVED_VALUE.fullmatch(value) is not None
        )

    def synthetic_list(value: object, *, size: int | None = None) -> bool:
        return (
            type(value) is list
            and bool(value)
            and (size is None or len(value) == size)
            and all(synthetic_label(item) for item in value)
        )

    def current_identifier(value: object, value_type: type) -> bool:
        if not synthetic_label(value):
            return False
        try:
            value_type(value)
        except (TypeError, ValueError):
            return False
        return True

    if "target" in inputs:
        target = inputs["target"]
        require(current_identifier(target["adapter"], AdapterId))
        require(current_identifier(target["connection"], ConnectionId))
        require(current_identifier(target["library"], LibraryId))
        require(current_identifier(target["external_item"], ExternalItemId))

    if category == "repeated_candidates":
        require(synthetic_list(inputs["candidate_values"], size=2))
        require(synthetic_list(inputs["evidence_families"], size=2))
        require(len(set(inputs["candidate_values"])) == 1)
        require(len(set(inputs["evidence_families"])) == 1)
        require(expected == {"agreement_families": 1, "outcome": "not_consensus"})
    elif category == "evidence_only_input":
        require(current_identifier(inputs["case_id"], ReviewCaseId))
        require(current_identifier(inputs["evidence_id"], EvidenceId))
        require(current_identifier(inputs["snapshot_id"], SnapshotId))
        require(inputs["proposals"] == [])
        require(expected == {"exception": "ValueError", "outcome": "reject_empty_case"})
    elif category == "consensus":
        require(synthetic_list(inputs["candidate_values"], size=2))
        require(synthetic_list(inputs["evidence_families"], size=2))
        require(len(set(inputs["candidate_values"])) == 1)
        require(len(set(inputs["evidence_families"])) == 2)
        require(expected == {"agreement_families": 2, "outcome": "consensus"})
    elif category == "conflicts":
        require(synthetic_label(inputs["candidate_identity"]) and synthetic_label(inputs["current_identity"]))
        require(inputs["candidate_identity"] != inputs["current_identity"])
        require(inputs["field"] == "title" and expected == {"outcome": "reject_candidate"})
    elif category == "no_ops":
        require(synthetic_list(inputs["current"], size=1) and synthetic_list(inputs["proposed"], size=1))
        require(inputs["current"] == inputs["proposed"])
        require(inputs["field"] == "title")
        require(current_identifier(inputs["evidence_id"], EvidenceId))
        require(current_identifier(inputs["proposal_id"], ProposalId))
        require(current_identifier(inputs["snapshot_id"], SnapshotId))
        require(expected == {"current_equals_proposed": True, "kind": "no_change"})
    elif category == "unloaded_current_values":
        require(inputs["current_state"] == "unloaded" and inputs["field"] == "publisher")
        require(synthetic_list(inputs["proposed"], size=1))
        require(expected == {"outcome": "unloaded_current"})
    elif category == "incompatible_fields":
        require(inputs["field"] == "narrators" and inputs["media_type"] == "ebook")
        require(synthetic_list(inputs["proposed"], size=1))
        require(expected == {"outcome": "inapplicable"})
    elif category == "cover_choices":
        require(all(synthetic_label(inputs[key]) for key in ("candidate", "current", "work_identity")))
        require(inputs["candidate"] != inputs["current"])
        require(expected == {"outcome": "typed_choice_without_change"})
    elif category == "series_choices":
        require(synthetic_list(inputs["raw_membership"], size=1))
        require(
            len(inputs["raw_sequence"]) == 1
            and _DECIMAL_SEQUENCE.fullmatch(inputs["raw_sequence"][0]) is not None
        )
        require(expected["membership"] == inputs["raw_membership"])
        require(expected["sequence"] == inputs["raw_sequence"])
        require(expected["outcome"] == "independent_series_parts")
    elif category == "independent_siblings":
        require(all(synthetic_label(inputs[key]) for key in ("confidence", "label", "link_method")))
        require(
            type(inputs["siblings"]) is list
            and len(inputs["siblings"]) == 2
            and len(set(inputs["siblings"])) == 2
            and all(current_identifier(item, ReviewCaseId) for item in inputs["siblings"])
        )
        require(expected == {"decision_state_present": False, "siblings": inputs["siblings"]})
    elif category == "destructive_cases":
        require(current_identifier(inputs["case_id"], ReviewCaseId))
        require(current_identifier(inputs["proposal_id"], ProposalId))
        require(current_identifier(inputs["snapshot_id"], SnapshotId))
        require(inputs["destructive"] is True)
        require(expected == {"exception": "ValueError", "outcome": "exclude_from_ordinary_review"})


def _validate_scenario(value: dict[str, Any], raw: bytes, *, filename: str, ordinal: int) -> Scenario:
    candidate_id = value.get("scenario_id") if type(value.get("scenario_id")) is str else None
    scenario_id = candidate_id if candidate_id and _SCENARIO_ID.fullmatch(candidate_id) else None
    if "provenance" not in value:
        _fail("DUX013_PROVENANCE", scenario=scenario_id, ordinal=ordinal)
    if "licensing" not in value:
        _fail("DUX014_LICENCE", scenario=scenario_id, ordinal=ordinal)
    _exact_keys(value, _SCENARIO_KEYS, "DUX010_SCENARIO_SCHEMA", scenario=scenario_id, ordinal=ordinal)
    if value["schema"] != SCHEMA_VERSION or scenario_id is None:
        _fail("DUX010_SCENARIO_SCHEMA", scenario=scenario_id, ordinal=ordinal)
    if value["category"] not in CATEGORIES:
        _fail("DUX012_CATEGORY", scenario=scenario_id, ordinal=ordinal)
    for key in ("dux_behavior", "preserved_invariant"):
        if type(value[key]) is not str or not value[key]:
            _fail("DUX010_SCENARIO_SCHEMA", scenario=scenario_id, ordinal=ordinal)

    provenance = _exact_keys(
        value["provenance"],
        frozenset({"source_commit", "source_tag", "behavioral_treatment", "fixture_origin"}),
        "DUX013_PROVENANCE",
        scenario=scenario_id,
        ordinal=ordinal,
    )
    if provenance != {
        "source_commit": SOURCE_COMMIT,
        "source_tag": SOURCE_TAG,
        "behavioral_treatment": "independently_reimplemented",
        "fixture_origin": "generated",
    }:
        _fail("DUX013_PROVENANCE", scenario=scenario_id, ordinal=ordinal)
    licensing = _exact_keys(
        value["licensing"],
        frozenset({"spdx", "authorship", "required_notice"}),
        "DUX014_LICENCE",
        scenario=scenario_id,
        ordinal=ordinal,
    )
    if licensing != {
        "spdx": "AGPL-3.0-or-later",
        "authorship": "Mechanical Librarian contributors and repository history",
        "required_notice": None,
    }:
        _fail("DUX014_LICENCE", scenario=scenario_id, ordinal=ordinal)

    status = value["status"]
    owner = value["future_owner"]
    not_yet = value["not_yet_proving_product_behavior"]
    if value["category"] in CURRENT_CATEGORIES:
        if status != "current" or owner is not None or not_yet is not False:
            _fail("DUX015_OWNERSHIP", scenario=scenario_id, ordinal=ordinal)
    else:
        if status != "future" or not_yet is not True:
            _fail("DUX015_OWNERSHIP", scenario=scenario_id, ordinal=ordinal)
        owner_value = _exact_keys(
            owner,
            frozenset({"issue", "state", "criterion"}),
            "DUX015_OWNERSHIP",
            scenario=scenario_id,
            ordinal=ordinal,
        )
        if (
            type(owner_value["issue"]) is not int
            or owner_value["state"] != "open"
            or owner_value["criterion"] not in _FUTURE_CRITERIA.get(owner_value["issue"], ())
        ):
            _fail("DUX015_OWNERSHIP", scenario=scenario_id, ordinal=ordinal)

    _audit(value, scenario=scenario_id, ordinal=ordinal)
    contract = _exact_keys(
        value["contract"],
        frozenset({"kind", "input", "expected"}),
        "DUX016_CONTRACT",
        scenario=scenario_id,
        ordinal=ordinal,
    )
    if type(contract["kind"]) is not str or type(contract["input"]) is not dict or type(contract["expected"]) is not dict:
        _fail("DUX016_CONTRACT", scenario=scenario_id, ordinal=ordinal)
    _validate_contract_shape(value["category"], contract, scenario=scenario_id, ordinal=ordinal)
    frozen = _freeze(value)
    return Scenario(scenario_id, value["category"], status, frozen["contract"], frozen, raw)


def load_registry(root: Path = CORPUS_ROOT) -> CorpusRegistry:
    """Validate every owned corpus file before returning an immutable registry."""

    registry_path = root / "registry.json"
    registry, registry_raw = _read_json(registry_path, ordinal=0)
    _exact_keys(
        registry,
        frozenset({"schema", "scenario_files", "coverage"}),
        "DUX006_REGISTRY_SCHEMA",
        scenario=None,
        ordinal=0,
    )
    if registry["schema"] != SCHEMA_VERSION:
        _fail("DUX006_REGISTRY_SCHEMA", ordinal=0)
    files = registry["scenario_files"]
    coverage = registry["coverage"]
    if (
        type(files) is not list
        or not files
        or any(type(name) is not str or not _SAFE_FILE.fullmatch(name) for name in files)
        or files != sorted(files)
        or len(files) != len(set(files))
    ):
        _fail("DUX007_REGISTRY_FILES", ordinal=0)
    if type(coverage) is not dict or frozenset(coverage) != CATEGORIES or any(type(item) is not str for item in coverage.values()):
        _fail("DUX008_COVERAGE", ordinal=0)
    try:
        entries = tuple(root.iterdir())
        actual = {path.name for path in entries}
    except OSError:
        _fail("DUX001_FILE_READ", ordinal=0)
    expected = {"registry.json", *files}
    if actual != expected or any(path.is_symlink() or not path.is_file() for path in entries):
        _fail("DUX009_ORPHAN_FILE", ordinal=0)

    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for ordinal, filename in enumerate(files, start=1):
        value, raw = _read_json(root / filename, ordinal=ordinal)
        scenario = _validate_scenario(value, raw, filename=filename, ordinal=ordinal)
        if scenario.scenario_id in seen:
            _fail("DUX017_DUPLICATE_ID", scenario=scenario.scenario_id, ordinal=ordinal)
        seen.add(scenario.scenario_id)
        scenarios.append(scenario)
    by_category = {scenario.category: scenario.scenario_id for scenario in scenarios}
    if len(scenarios) != len(CATEGORIES) or by_category != coverage:
        _fail("DUX008_COVERAGE", ordinal=0)
    by_id = MappingProxyType({scenario.scenario_id: scenario for scenario in scenarios})
    return CorpusRegistry(tuple(scenarios), by_id, MappingProxyType(dict(coverage)), registry_raw)


def load_scenario(scenario_id: str, root: Path = CORPUS_ROOT) -> Scenario:
    """Return one scenario only after complete-registry validation succeeds."""

    registry = load_registry(root)
    if type(scenario_id) is not str or not _SCENARIO_ID.fullmatch(scenario_id) or scenario_id not in registry.by_id:
        _fail("DUX018_UNKNOWN_SCENARIO", scenario=scenario_id if type(scenario_id) is str else None, ordinal=0)
    return registry.by_id[scenario_id]
