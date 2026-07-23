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
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.identifiers import Fingerprint

_PACKAGE_FIELDS = {
    EpubPackageField.TITLE: EpubInspectionField.TITLE, EpubPackageField.SUBTITLE: EpubInspectionField.SUBTITLE,
    EpubPackageField.CREATOR: EpubInspectionField.CREATOR, EpubPackageField.CONTRIBUTOR: EpubInspectionField.CONTRIBUTOR,
    EpubPackageField.COLLECTION: EpubInspectionField.SERIES, EpubPackageField.COLLECTION_POSITION: EpubInspectionField.SERIES_SEQUENCE,
    EpubPackageField.PUBLICATION_DATE: EpubInspectionField.PUBLICATION_DATE, EpubPackageField.PUBLISHER: EpubInspectionField.PUBLISHER,
    EpubPackageField.DESCRIPTION: EpubInspectionField.DESCRIPTION, EpubPackageField.LANGUAGE: EpubInspectionField.LANGUAGE,
    EpubPackageField.SUBJECT: EpubInspectionField.SUBJECT,
}
_MARKER = re.compile(r"(?:copyright|©|all rights reserved)", re.I)
_BYLINE = re.compile(r"^(?:by|author\s*[:\-]?|written by)\s+(.+)$", re.I)


def _bounded(value: str) -> tuple[str, str | None]:
    if value.startswith("unsafe-source-text-sha256:"):
        return value, "unsupported"
    if len(value) <= 280:
        return value, None
    return "oversized-source-text-sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest(), "oversized"


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
    for observation in report.observations:
        field = _PACKAGE_FIELDS.get(observation.field)
        if field is None and observation.field is not EpubPackageField.IDENTIFIER:
            continue
        observed, forced_state = _bounded(observation.raw_value)
        canonical = observation.normalized_value if forced_state is None else None
        if observation.field is EpubPackageField.IDENTIFIER:
            field = EpubInspectionField.ISBN
            valid = canonical is not None and len(canonical) in {10, 13} and opening._isbn_valid(canonical)
            checksum = f"valid_isbn_{len(canonical)}" if valid else "invalid"
            if not valid:
                canonical = None
            state = forced_state or ("valid" if valid else "malformed")
            facts.append(EpubInspectionFact(field, source, observed, canonical, observed, checksum, "unknown", "unlabelled", "package_metadata", state=state))
            continue
        state = forced_state or ("inapplicable" if observation.applicability.value == "inapplicable" else "untyped" if field is EpubInspectionField.SUBJECT else "malformed" if canonical is None else "valid")
        facts.append(EpubInspectionFact(field, source, observed, canonical, observed, "not_applicable", "not_applicable", "not_applicable", "package_metadata", state=state, role=roles.get(observation.citation.source_ordinal)))
    return tuple(facts)


def rear_facts(request, report: EpubPackageReport, opening_report: EpubOpeningReport, archive: zipfile.ZipFile) -> tuple[EpubInspectionFact, ...]:
    if not report.succeeded or report.source_fingerprint is None:
        return ()
    manifests = {d.resource_identity: d for d in report.declarations if d.kind is EpubPackageDeclarationKind.MANIFEST_ITEM and d.resource_identity}
    spine = [d for d in report.declarations if d.kind is EpubPackageDeclarationKind.SPINE_SLOT]
    encrypted = {d.resource_identity for d in report.declarations if d.kind is EpubPackageDeclarationKind.ENCRYPTION and d.resource_identity}
    opened = {r.resource_identity for r in opening_report.resources if r.opened}
    seen: set[str] = set()
    limits = EpubOpeningLimits(package=request.limits, selected_documents=4, expanded_bytes_per_resource=1_048_576, expanded_bytes_per_selection=4_194_304, text_characters_per_selection=262_144, nodes_per_resource=50_000, nodes_per_selection=50_000, facts=16)
    names = {epub_resource_identity(info.filename): info.filename for info in archive.infolist() if not info.is_dir()}
    facts: list[EpubInspectionFact] = []; expanded = text = nodes = opens = 0
    for tail_slot, spine_item in enumerate(reversed(spine[-8:]), 1):
        opening._check(request)
        identity = spine_item.resource_identity
        if identity is None or not spine_item.linear:
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
        if opens >= 4:
            break
        opens += 1
        try:
            data = opening._read_member(archive, names[identity], limits.expanded_bytes_per_resource, request)
            expanded += len(data)
            if expanded > limits.expanded_bytes_per_selection: raise opening._AggregateRejected(report.outcome.RESOURCE_LIMIT)
            projection = opening._parse(data, declaration.media_type, limits, request)
            text += projection.work_text_characters; nodes += projection.work_nodes
            if text > limits.text_characters_per_selection or nodes > limits.nodes_per_selection: raise opening._AggregateRejected(report.outcome.RESOURCE_LIMIT)
        except opening._AggregateRejected: raise
        except (opening._ResourceRejected, opening._MalformedMarkup, opening._UnsafeConstruct, opening._UnsupportedEncoding, ValueError): continue
        marker = _MARKER.search(projection.text)
        if marker is None: continue
        provenance = EpubRearProvenance(identity, spine_item.spine_position or tail_slot, tail_slot, marker.group(0).lower(), marker.group(0))
        parents = {a for element in projection.elements for a in element.ancestors}; resource_isbns = 0
        for element in projection.elements:
            if element.ordinal in parents: continue
            value = " ".join(element.exact_text.split())
            if not value or len(value) > 280: continue
            field = canonical = None; role = None
            if element.semantics & {"title", "fulltitle"}: field, canonical = EpubInspectionField.TITLE, value
            elif element.semantics & {"contributor"}: field, canonical = EpubInspectionField.CONTRIBUTOR, value
            elif element.semantics & {"creator"}: field, canonical = EpubInspectionField.CREATOR, value
            elif (byline := _BYLINE.match(value)) is not None and len(byline.group(1)) <= 280: field, canonical = EpubInspectionField.CREATOR, byline.group(1)
            if field is not None: facts.append(EpubInspectionFact(field, report.source_fingerprint, value, canonical, value, "not_applicable", "not_applicable", "not_applicable", "opening_matter", rear_provenance=provenance, role=role))
            for match in opening._ISBN.finditer(value):
                if resource_isbns >= 4: break
                isbn = opening._normal(match.group(0), opening.OpeningField.IDENTIFIER)
                if len(isbn) in {10, 13} and opening._isbn_valid(isbn):
                    facts.append(EpubInspectionFact(EpubInspectionField.ISBN, report.source_fingerprint, match.group(0), isbn, value, f"valid_isbn_{len(isbn)}", "unknown", "unlabelled", "opening_matter", rear_provenance=provenance)); resource_isbns += 1
            if len(facts) > 16: raise opening._AggregateRejected(report.outcome.RESOURCE_LIMIT)
    return tuple(facts)
