"""EPUB-local evidence projection kept beside the provider parsers."""

from __future__ import annotations

import hashlib
import re
import zipfile

from mechanical_librarian.adapters.evidence_extractors import epub_opening as opening
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageDeclarationKind, EpubPackageField, EpubPackageReport, epub_resource_identity,
)
from mechanical_librarian.domain.epub_inspection import (
    EpubInspectionFact, EpubInspectionField, EpubPackageLimits, EpubRearProvenance,
)
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError, validate_projection_text, validate_role_projection
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.identifiers import Fingerprint
from mechanical_librarian.domain.metadata_report import normalize_title

_PACKAGE_FIELDS = {
    EpubPackageField.TITLE: EpubInspectionField.TITLE, EpubPackageField.SUBTITLE: EpubInspectionField.SUBTITLE,
    EpubPackageField.CREATOR: EpubInspectionField.CREATOR, EpubPackageField.CONTRIBUTOR: EpubInspectionField.CONTRIBUTOR,
    EpubPackageField.COLLECTION: EpubInspectionField.SERIES, EpubPackageField.COLLECTION_POSITION: EpubInspectionField.SERIES_SEQUENCE,
    EpubPackageField.PUBLICATION_DATE: EpubInspectionField.PUBLICATION_DATE, EpubPackageField.PUBLISHER: EpubInspectionField.PUBLISHER,
    EpubPackageField.DESCRIPTION: EpubInspectionField.DESCRIPTION, EpubPackageField.LANGUAGE: EpubInspectionField.LANGUAGE,
    EpubPackageField.SUBJECT: EpubInspectionField.SUBJECT,
}
_NON_BIBLIOGRAPHIC_CONTRIBUTOR_ROLES = {"bkp"}
_MARKER = re.compile(r"(?:copyright|©|all rights reserved)", re.I)
_BYLINE = re.compile(r"^(?:by|author\s*[:\-]?|written by)\s+(.+)$", re.I)
_SENTINEL = re.compile(r"(?:unsafe|oversized)-source-text-sha256:[0-9a-f]{64}")
_ISBN_LABEL = re.compile(r"^(?:urn:isbn:|isbn(?:-1[03])?\s*:?)", re.I)


def _bounded(value: str) -> tuple[str, str | None]:
    if _SENTINEL.fullmatch(value) and value.startswith("unsafe-"):
        return value, "unsupported"
    if _SENTINEL.fullmatch(value) and value.startswith("oversized-"):
        return value, "oversized"
    if len(value) <= 280:
        return value, None
    return "oversized-source-text-sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest(), "oversized"


def _project_role(value: str | None) -> str | None:
    """Project package role text without allowing arbitrary source text through."""

    if value is None:
        return None
    if _SENTINEL.fullmatch(value):
        return value
    if len(value) > 64:
        return "oversized-source-text-sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    try:
        return validate_role_projection(value)
    except EvidenceValueError:
        return "unsafe-source-text-sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def package_facts(report: EpubPackageReport, source: Fingerprint) -> tuple[EpubInspectionFact, ...]:
    facts: list[EpubInspectionFact] = []
    values: dict[EpubPackageField, list[tuple[int, str | None]]] = {EpubPackageField.CREATOR: [], EpubPackageField.CONTRIBUTOR: []}
    for observation in report.observations:
        if observation.field in values:
            values[observation.field].append((observation.citation.source_ordinal, None))
        if observation.field in {EpubPackageField.CREATOR_ROLE, EpubPackageField.CONTRIBUTOR_ROLE}:
            target = EpubPackageField.CREATOR if observation.field is EpubPackageField.CREATOR_ROLE else EpubPackageField.CONTRIBUTOR
            if values[target]:
                ordinal, _ = values[target][-1]
                values[target][-1] = (ordinal, observation.normalized_value)
    roles = {ordinal: role for entries in values.values() for ordinal, role in entries}
    identifier_schemes = {
        observation.citation.source_ordinal: observation.normalized_value
        for observation in report.observations
        if observation.field is EpubPackageField.IDENTIFIER_SCHEME
    }
    package_identifier_ordinals = {
        observation.citation.source_ordinal
        for observation in report.observations
        if observation.field is EpubPackageField.PACKAGE_IDENTIFIER
    }
    if any(
        warning.code == "inferred_package_identifier"
        for warning in report.warnings
    ):
        package_identifier_ordinals.clear()
    for observation in report.observations:
        field = _PACKAGE_FIELDS.get(observation.field)
        if field is None and observation.field is not EpubPackageField.IDENTIFIER:
            continue
        observed, forced_state = _bounded(observation.raw_value)
        canonical = observation.normalized_value if forced_state is None else None
        if observation.field is EpubPackageField.IDENTIFIER:
            field = EpubInspectionField.ISBN
            valid = canonical is not None and len(canonical) in {10, 13} and opening._isbn_valid(canonical)
            scheme = identifier_schemes.get(observation.citation.source_ordinal)
            scheme_key = None if scheme is None else scheme.casefold()
            explicit_isbn = _ISBN_LABEL.match(observation.raw_value) is not None or scheme_key in {"isbn", "isbn-10", "isbn-13"}
            if scheme_key is not None and scheme_key not in {"isbn", "isbn-10", "isbn-13"}:
                continue
            if not valid and not explicit_isbn:
                continue
            if not valid:
                canonical = None
            primary = (
                observation.citation.source_ordinal
                in package_identifier_ordinals
            )
            facts.append(
                EpubInspectionFact(
                    field,
                    source,
                    observed,
                    canonical,
                    observed,
                    f"valid_isbn_{len(canonical)}" if valid else "invalid",
                    "ebook" if primary else "unknown",
                    "ebook" if primary else "unlabelled",
                    "package_metadata",
                    state=forced_state or ("valid" if valid else "malformed"),
                )
            )
            continue
        if canonical is not None and field in {EpubInspectionField.TITLE, EpubInspectionField.SUBTITLE}:
            canonical = normalize_title(observed)
        state = forced_state or ("untyped" if field is EpubInspectionField.SUBJECT else "malformed" if canonical is None else "inapplicable" if observation.applicability.value == "inapplicable" else "valid")
        role = _project_role(roles.get(observation.citation.source_ordinal))
        if (
            field is EpubInspectionField.CONTRIBUTOR
            and role in _NON_BIBLIOGRAPHIC_CONTRIBUTOR_ROLES
        ):
            continue
        facts.append(EpubInspectionFact(field, source, observed, canonical, observed, "not_applicable", "not_applicable", "not_applicable", "package_metadata", state=state, role=role))
    return tuple(facts)


def rear_facts(request, report: EpubPackageReport, opening_report: EpubOpeningReport, archive: zipfile.ZipFile) -> tuple[EpubInspectionFact, ...]:
    if not report.succeeded or report.source_fingerprint is None:
        return ()
    manifests = {d.resource_identity: d for d in report.declarations if d.kind is EpubPackageDeclarationKind.MANIFEST_ITEM and d.resource_identity}
    # Select the tail from the linear spine first. Non-linear declarations do
    # not displace a linear tail position; every selected linear slot still
    # consumes its fixed position before later eligibility checks.
    spine = [d for d in report.declarations if d.kind is EpubPackageDeclarationKind.SPINE_SLOT and d.linear]
    encrypted = {d.resource_identity for d in report.declarations if d.kind is EpubPackageDeclarationKind.ENCRYPTION and d.resource_identity}
    opened = {r.resource_identity for r in opening_report.resources if r.opened}
    rear_capacity = min(4, max(0, 9 - opening_report.usage.opened_resources))
    seen: set[str] = set()
    limits = EpubOpeningLimits(package=request.limits, selected_documents=4, expanded_bytes_per_resource=1_048_576, expanded_bytes_per_selection=4_194_304, text_characters_per_selection=262_144, nodes_per_resource=50_000, nodes_per_selection=50_000, facts=16)
    names = {epub_resource_identity(info.filename): info.filename for info in archive.infolist() if not info.is_dir()}
    facts: list[EpubInspectionFact] = []; expanded = text = nodes = opens = 0
    for tail_slot, spine_item in enumerate(reversed(spine[-8:]), 1):
        opening._check(request)
        identity = spine_item.resource_identity
        if identity is None:
            continue
        # A tail position is consumed before eligibility checks.  A repeated
        # identity therefore cannot re-open or re-emit evidence from an earlier
        # tail slot.
        if identity in seen:
            continue
        seen.add(identity)
        if identity in opened:
            continue
        declaration = manifests.get(identity)
        if declaration is None or identity in encrypted or declaration.media_type not in {"application/xhtml+xml", "text/html"} or identity not in names:
            continue
        if opens >= rear_capacity:
            break
        opens += 1
        try:
            data = opening._read_member(archive, names[identity], limits.expanded_bytes_per_resource, request)
            expanded += len(data)
            if expanded > limits.expanded_bytes_per_selection: raise opening._AggregateRejected(report.outcome.RESOURCE_LIMIT)
            projection = opening._parse(data, declaration.media_type, limits, request)
            text += projection.work_text_characters; nodes += projection.work_nodes
            if text > limits.text_characters_per_selection or nodes > limits.nodes_per_selection: raise opening._AggregateRejected(report.outcome.RESOURCE_LIMIT)
        except opening._AggregateRejected:
            raise
        except opening._ResourceRejected:
            raise opening._AggregateRejected(report.outcome.RESOURCE_LIMIT) from None
        except (opening._MalformedMarkup, opening._UnsafeConstruct, opening._UnsupportedEncoding, ValueError):
            raise opening._AggregateRejected(report.outcome.MALFORMED) from None
        marker = _MARKER.search(projection.text)
        if marker is None: continue
        provenance = EpubRearProvenance(identity, spine_item.spine_position or tail_slot, tail_slot, marker.group(0).lower(), marker.group(0))
        parents = {a for element in projection.elements for a in element.ancestors}; resource_isbns = 0
        for element in projection.elements:
            is_ancestor = element.ordinal in parents
            value = " ".join(element.exact_text.split())
            if not value or len(value) > 280: continue
            try:
                value = validate_projection_text(value, "rear-spine cue", 280)
            except EvidenceValueError:
                continue
            field = canonical = None; role = None
            byline = _BYLINE.match(value)
            admitted_cue = bool(element.semantics & {"title", "fulltitle", "contributor", "creator"}) or byline is not None
            if is_ancestor and not admitted_cue: continue
            if element.semantics & {"title", "fulltitle"}: field, canonical = EpubInspectionField.TITLE, normalize_title(value)
            elif element.semantics & {"contributor"}: field, canonical = EpubInspectionField.CONTRIBUTOR, value
            elif byline is not None and len(byline.group(1)) <= 280: field, canonical, role = EpubInspectionField.CREATOR, byline.group(1), "author"
            elif element.semantics & {"creator"}: field, canonical, role = EpubInspectionField.CREATOR, value, "author"
            if field is not None: facts.append(EpubInspectionFact(field, report.source_fingerprint, value, canonical, value, "not_applicable", "not_applicable", "not_applicable", "opening_matter", rear_provenance=provenance, role=role))
            owner_spans = opening._non_isbn_identifier_spans(value)
            for match in () if is_ancestor else opening._ISBN.finditer(value):
                if resource_isbns >= 4: break
                if opening._isbn_owned_by_non_isbn_identifier(match, owner_spans):
                    continue
                isbn = opening._normal(match.group(0), opening.OpeningField.IDENTIFIER)
                if len(isbn) in {10, 13} and opening._isbn_valid(isbn):
                    facts.append(EpubInspectionFact(EpubInspectionField.ISBN, report.source_fingerprint, match.group(0), isbn, value, f"valid_isbn_{len(isbn)}", "unknown", "unlabelled", "opening_matter", rear_provenance=provenance)); resource_isbns += 1
            if len(facts) > 16: raise opening._AggregateRejected(report.outcome.RESOURCE_LIMIT)
    return tuple(facts)
