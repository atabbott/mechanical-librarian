"""EPUB-local evidence projection kept beside the provider parsers."""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import date, datetime
from typing import cast

from mechanical_librarian.adapters.evidence_extractors import epub_opening as opening
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageDeclarationKind,
    EpubPackageField,
    EpubPackageReport,
    EpubPackageRequest,
    epub_resource_identity,
)
from mechanical_librarian.domain.epub_inspection import (
    DateEvent,
    DatePrecision,
    EpubInspectionFact,
    EpubInspectionField,
    EpubRearProvenance,
    normalize_date_value,
)
from mechanical_librarian.domain.epub_opening import (
    EpubOpeningLimits,
    EpubOpeningReport,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
    validate_role_projection,
)
from mechanical_librarian.domain.identifiers import Fingerprint
from mechanical_librarian.domain.metadata_report import normalize_title

_PACKAGE_FIELDS = {
    EpubPackageField.TITLE: EpubInspectionField.TITLE,
    EpubPackageField.SUBTITLE: EpubInspectionField.SUBTITLE,
    EpubPackageField.CREATOR: EpubInspectionField.CREATOR,
    EpubPackageField.CONTRIBUTOR: EpubInspectionField.CONTRIBUTOR,
    EpubPackageField.COLLECTION: EpubInspectionField.SERIES,
    EpubPackageField.COLLECTION_POSITION: EpubInspectionField.SERIES_SEQUENCE,
    EpubPackageField.PUBLICATION_DATE: EpubInspectionField.PUBLICATION_DATE,
    EpubPackageField.PUBLISHER: EpubInspectionField.PUBLISHER,
    EpubPackageField.COPYRIGHT_DATE: EpubInspectionField.DATE_EVENT,
    EpubPackageField.CREATION_DATE: EpubInspectionField.DATE_EVENT,
    EpubPackageField.MODIFICATION_DATE: EpubInspectionField.DATE_EVENT,
    EpubPackageField.DESCRIPTION: EpubInspectionField.DESCRIPTION,
    EpubPackageField.LANGUAGE: EpubInspectionField.LANGUAGE,
    EpubPackageField.SUBJECT: EpubInspectionField.SUBJECT,
}
_ROLE_MAP = {
    "aut": "author",
    "author": "author",
    "trl": "translator",
    "translator": "translator",
    "ill": "illustrator",
    "illustrator": "illustrator",
    "edt": "editor",
    "editor": "editor",
    "aui": "foreword",
    "foreword": "foreword",
    "dsr": "designer",
    "designer": "designer",
    "pht": "photographer",
    "photographer": "photographer",
    "ctg": "cartographer",
    "cartographer": "cartographer",
    "adp": "adapter",
    "adapter": "adapter",
}
_CONTEXTUAL_ROLE_MAP = {
    "bkp": "book_producer",
    "book producer": "book_producer",
    "converter": "conversion",
    "scanner": "scanner",
}
_MARKER = re.compile(
    r"(?:copyright|©|all rights reserved|\bcolophon\b|\bcredits\b)", re.I
)
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
    return "oversized-source-text-sha256:" + hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest(), "oversized"


def _project_role(value: str | None) -> str | None:
    """Project package role text without allowing arbitrary source text through."""

    if value is None:
        return None
    if _SENTINEL.fullmatch(value):
        return value
    key = " ".join(value.casefold().split())
    if key in _ROLE_MAP:
        return _ROLE_MAP[key]
    if key in _CONTEXTUAL_ROLE_MAP:
        return _CONTEXTUAL_ROLE_MAP[key]
    # An otherwise safe role arrived through an explicit package role
    # attribute/refinement. Retain that structured fact without guessing a
    # prose role.
    if re.fullmatch(r"[a-z][a-z0-9 ._-]{0,63}", key):
        return "other"
    if len(value) > 64:
        return (
            "oversized-source-text-sha256:"
            + hashlib.sha256(value.encode("utf-8")).hexdigest()
        )
    try:
        return validate_role_projection(value)
    except EvidenceValueError:
        return (
            "unsafe-source-text-sha256:"
            + hashlib.sha256(value.encode("utf-8")).hexdigest()
        )


def _package_date_precision(value: str | None) -> DatePrecision | None:
    """Retain precision for every lexical form admitted by the package parser."""

    if value is None:
        return None
    try:
        if "T" in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return DatePrecision.TIMESTAMP
        if len(value) == 4 and value.isdigit():
            return DatePrecision.YEAR
        if len(value) == 7:
            date(int(value[:4]), int(value[5:]), 1)
            return DatePrecision.MONTH
        if len(value) == 10:
            date.fromisoformat(value)
            return DatePrecision.DAY
    except ValueError:
        return None
    return None


def package_facts(
    report: EpubPackageReport, source: Fingerprint
) -> tuple[EpubInspectionFact, ...]:
    facts: list[EpubInspectionFact] = []
    values: dict[EpubPackageField, list[tuple[int, list[str]]]] = {
        EpubPackageField.CREATOR: [],
        EpubPackageField.CONTRIBUTOR: [],
    }
    for observation in report.observations:
        if observation.field in values:
            values[observation.field].append((observation.citation.source_ordinal, []))
        if observation.field in {
            EpubPackageField.CREATOR_ROLE,
            EpubPackageField.CONTRIBUTOR_ROLE,
        }:
            target = (
                EpubPackageField.CREATOR
                if observation.field is EpubPackageField.CREATOR_ROLE
                else EpubPackageField.CONTRIBUTOR
            )
            if values[target]:
                ordinal, declared_roles = values[target][-1]
                role = _project_role(observation.normalized_value)
                if role is not None and role not in declared_roles:
                    declared_roles.append(role)
                values[target][-1] = (ordinal, declared_roles)
    roles = {
        ordinal: tuple(declared_roles)
        for entries in values.values()
        for ordinal, declared_roles in entries
    }
    identifier_schemes = {
        observation.citation.source_ordinal: observation.normalized_value
        for observation in report.observations
        if observation.field is EpubPackageField.IDENTIFIER_SCHEME
    }
    declared_date_events = {
        observation.citation.source_ordinal: observation.normalized_value.casefold()
        for observation in report.observations
        if observation.field is EpubPackageField.DATE_EVENT
        and observation.normalized_value is not None
    }
    package_identifier_ordinals = {
        observation.citation.source_ordinal
        for observation in report.observations
        if observation.field is EpubPackageField.PACKAGE_IDENTIFIER
    }
    if any(
        warning.code == "inferred_package_identifier" for warning in report.warnings
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
            valid = (
                canonical is not None
                and len(canonical) in {10, 13}
                and opening._isbn_valid(canonical)
            )
            scheme = identifier_schemes.get(observation.citation.source_ordinal)
            scheme_key = None if scheme is None else scheme.casefold()
            explicit_isbn = _ISBN_LABEL.match(
                observation.raw_value
            ) is not None or scheme_key in {"isbn", "isbn-10", "isbn-13"}
            if scheme_key is not None and scheme_key not in {
                "isbn",
                "isbn-10",
                "isbn-13",
            }:
                continue
            # A ``None`` scheme key is a legitimate lookup miss here.
            declared_length = cast(
                "dict[str | None, int]", {"isbn-10": 10, "isbn-13": 13}
            ).get(scheme_key)
            normalized_isbn = (
                ""
                if canonical is None
                else "".join(
                    character
                    for character in canonical.upper()
                    if character.isdigit() or character == "X"
                )
            )
            if (
                declared_length is not None
                and opening._isbn_valid(canonical or "")
                and len(normalized_isbn) != declared_length
            ):
                continue
            if not valid and not explicit_isbn:
                continue
            if not valid:
                canonical = None
            primary = observation.citation.source_ordinal in package_identifier_ordinals
            facts.append(
                EpubInspectionFact(
                    field,
                    source,
                    observed,
                    canonical,
                    observed,
                    # ``valid`` is only true when ``canonical`` is a string.
                    f"valid_isbn_{len(cast(str, canonical))}" if valid else "invalid",
                    "ebook" if primary else "unknown",
                    "ebook" if primary else "unlabelled",
                    "package_metadata",
                    state=forced_state or ("valid" if valid else "malformed"),
                )
            )
            continue
        assert field is not None
        if canonical is not None and field in {
            EpubInspectionField.TITLE,
            EpubInspectionField.SUBTITLE,
        }:
            canonical = normalize_title(observed)
        state = forced_state or (
            "untyped"
            if field is EpubInspectionField.SUBJECT
            else "malformed"
            if canonical is None
            else "inapplicable"
            if observation.applicability.value == "inapplicable"
            else "valid"
        )
        declared_roles = roles.get(observation.citation.source_ordinal, ())  # type: ignore[assignment]  # reason: this rebinds the earlier per-observation role list to the frozen role tuple built above; both are only iterated from here on.
        bibliographic_roles = tuple(
            role
            for role in declared_roles
            if role in set(_ROLE_MAP.values()) | {"other"}
        )
        contextual_roles = tuple(
            role for role in declared_roles if role in _CONTEXTUAL_ROLE_MAP.values()
        )
        sentinel_roles = tuple(
            role for role in declared_roles if _SENTINEL.fullmatch(role)
        )
        role = "+".join(bibliographic_roles) or (
            sentinel_roles[0] if sentinel_roles and not contextual_roles else None
        )
        role_parts = set(bibliographic_roles)
        non_author_bibliographic_roles = set(_ROLE_MAP.values()) - {"author"}
        if (
            field is EpubInspectionField.CREATOR
            and role_parts & non_author_bibliographic_roles
        ):
            field = EpubInspectionField.CONTRIBUTOR
        contextual_only = (
            observation.field
            in {EpubPackageField.CREATOR, EpubPackageField.CONTRIBUTOR}
            and bool(contextual_roles)
            and not bibliographic_roles
            and not sentinel_roles
        )
        date_event = {
            EpubPackageField.PUBLICATION_DATE: DateEvent.MANIFESTATION_PUBLICATION,
            EpubPackageField.COPYRIGHT_DATE: DateEvent.COPYRIGHT,
            EpubPackageField.CREATION_DATE: DateEvent.CREATION,
            EpubPackageField.MODIFICATION_DATE: DateEvent.MODIFICATION,
        }.get(observation.field)
        declared_date_event = declared_date_events.get(
            observation.citation.source_ordinal
        )
        if (
            observation.field is EpubPackageField.PUBLICATION_DATE
            and declared_date_event is not None
            and declared_date_event != "publication"
        ):
            date_event = {
                "first-publication": DateEvent.WORK_FIRST_PUBLICATION,
                "original-publication": DateEvent.WORK_FIRST_PUBLICATION,
                "conversion": DateEvent.CONVERSION,
            }.get(declared_date_event, DateEvent.UNKNOWN)
            field = EpubInspectionField.DATE_EVENT
        date_precision = (
            _package_date_precision(canonical or observed)
            if date_event is not None
            else None
        )
        if (
            date_event
            in {
                DateEvent.WORK_FIRST_PUBLICATION,
                DateEvent.CONVERSION,
            }
            and date_precision is not None
        ):
            canonical = canonical or observed
            state = forced_state or "valid"
        if date_event is not None and state == "valid":
            try:
                canonical, inferred_precision = normalize_date_value(observed)
            except ValueError:
                canonical = None
                date_precision = None
                state = "malformed"
            else:
                if inferred_precision is not date_precision:
                    canonical = None
                    date_precision = None
                    state = "malformed"
        if date_event is not None and state != "valid":
            canonical = None
            date_precision = None
        if not contextual_only:
            facts.append(
                EpubInspectionFact(
                    field,
                    source,
                    observed,
                    canonical,
                    observed,
                    "not_applicable",
                    "not_applicable",
                    "not_applicable",
                    "package_metadata",
                    state=state,
                    role=role,
                    date_event=date_event,
                    date_precision=date_precision,
                )
            )
        for contextual_role in contextual_roles:
            facts.append(
                EpubInspectionFact(
                    EpubInspectionField.CONTEXTUAL_CREDIT,
                    source,
                    observed,
                    canonical,
                    observed,
                    "not_applicable",
                    "not_applicable",
                    "not_applicable",
                    "package_metadata",
                    state=state,
                    role=contextual_role,
                )
            )
        if bibliographic_roles or contextual_roles:
            for sentinel_role in sentinel_roles:
                facts.append(
                    EpubInspectionFact(
                        EpubInspectionField.CONTEXTUAL_CREDIT,
                        source,
                        observed,
                        canonical,
                        observed,
                        "not_applicable",
                        "not_applicable",
                        "not_applicable",
                        "package_metadata",
                        state=state,
                        role=sentinel_role,
                    )
                )
    return tuple(facts)


def rear_facts(
    request: EpubPackageRequest,
    report: EpubPackageReport,
    opening_report: EpubOpeningReport,
    archive: zipfile.ZipFile,
) -> tuple[EpubInspectionFact, ...]:
    if not report.succeeded or report.source_fingerprint is None:
        return ()
    manifests = {
        d.resource_identity: d
        for d in report.declarations
        if d.kind is EpubPackageDeclarationKind.MANIFEST_ITEM and d.resource_identity
    }
    # Select the tail from the linear spine first. Non-linear declarations do
    # not displace a linear tail position; every selected linear slot still
    # consumes its fixed position before later eligibility checks.
    spine = [
        d
        for d in report.declarations
        if d.kind is EpubPackageDeclarationKind.SPINE_SLOT and d.linear
    ]
    encrypted = {
        d.resource_identity
        for d in report.declarations
        if d.kind is EpubPackageDeclarationKind.ENCRYPTION and d.resource_identity
    }
    opened = {r.resource_identity for r in opening_report.resources if r.opened}
    rear_capacity = min(4, max(0, 9 - opening_report.usage.opened_resources))
    seen: set[str] = set()
    limits = EpubOpeningLimits(
        package=request.limits,
        selected_documents=4,
        expanded_bytes_per_resource=1_048_576,
        expanded_bytes_per_selection=4_194_304,
        text_characters_per_selection=262_144,
        nodes_per_resource=50_000,
        nodes_per_selection=50_000,
        facts=16,
    )
    names = {
        epub_resource_identity(info.filename): info.filename
        for info in archive.infolist()
        if not info.is_dir()
    }
    facts: list[EpubInspectionFact] = []
    expanded = text = nodes = opens = 0
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
        if (
            declaration is None
            or identity in encrypted
            or declaration.media_type not in {"application/xhtml+xml", "text/html"}
            or identity not in names
        ):
            continue
        if opens >= rear_capacity:
            break
        opens += 1
        try:
            data = opening._read_member(
                archive, names[identity], limits.expanded_bytes_per_resource, request
            )
            expanded += len(data)
            if expanded > limits.expanded_bytes_per_selection:
                raise opening._AggregateRejected(
                    report.outcome.RESOURCE_LIMIT, tuple(facts)
                )
            projection = opening._parse(data, declaration.media_type, limits, request)
            text += projection.work_text_characters
            nodes += projection.work_nodes
            if (
                text > limits.text_characters_per_selection
                or nodes > limits.nodes_per_selection
            ):
                raise opening._AggregateRejected(
                    report.outcome.RESOURCE_LIMIT, tuple(facts)
                )
        except opening._AggregateRejected:
            raise
        except opening._ResourceRejected:
            raise opening._AggregateRejected(
                report.outcome.RESOURCE_LIMIT, tuple(facts)
            ) from None
        except (
            opening._MalformedMarkup,
            opening._UnsafeConstruct,
            opening._UnsupportedEncoding,
            ValueError,
        ):
            raise opening._AggregateRejected(report.outcome.MALFORMED) from None
        promotion_exclusions = opening._promotion_excluded_ordinals(projection)
        by_ordinal = {element.ordinal: element for element in projection.elements}
        parents = {
            ancestor
            for element in projection.elements
            for ancestor in element.ancestors
        }
        promotion_ancestors = {
            ancestor
            for ordinal in promotion_exclusions
            for ancestor in by_ordinal[ordinal].ancestors
        }
        marker = next(
            (
                match
                for element in projection.elements
                if (
                    element.ordinal not in promotion_exclusions
                    and element.ordinal not in promotion_ancestors
                )
                for match in [_MARKER.search(element.exact_text)]
                if match is not None
            ),
            None,
        )
        if marker is None:
            continue
        provenance = EpubRearProvenance(
            identity,
            spine_item.spine_position or tail_slot,
            tail_slot,
            marker.group(0).lower(),
            marker.group(0),
        )
        resource_isbns = 0
        eligible_ordinals: set[int] = set()
        for candidate in projection.elements:
            if candidate.ordinal in promotion_exclusions:
                continue
            candidate_value = " ".join(candidate.exact_text.split())
            if not candidate_value or len(candidate_value) > 280:
                continue
            try:
                validate_projection_text(
                    candidate_value,
                    "rear-spine cue",
                    280,
                )
            except EvidenceValueError:
                continue
            eligible_ordinals.add(candidate.ordinal)
        (
            bibliographic_labels,
            bibliographic_label_allocations,
        ) = opening._bibliographic_indicia_label_allocations(
            projection.elements,
            eligible_ordinals,
        )
        bibliographic_owners: list[tuple[int, EpubInspectionField, str]] = []
        for element in projection.elements:
            if element.ordinal in promotion_exclusions:
                continue
            is_ancestor = element.ordinal in parents
            value = " ".join(element.exact_text.split())
            if not value or len(value) > 280:
                continue
            try:
                value = validate_projection_text(value, "rear-spine cue", 280)
            except EvidenceValueError:
                continue
            field = canonical = None
            role = None
            byline = _BYLINE.match(value)
            bibliographic_label = opening._is_bibliographic_indicia_label(value)
            bibliographic_indicia = opening._bibliographic_indicia(
                element.semantics,
                value,
                bibliographic_labels.get(element.ordinal, ()),
            )
            admitted_cue = (
                bool(
                    element.semantics & {"title", "fulltitle", "contributor", "creator"}
                )
                or byline is not None
                or bool(bibliographic_indicia)
            )
            credits = () if is_ancestor else opening._indicia_credits(value)
            dates = () if is_ancestor else opening._indicia_dates(value)
            admitted_cue = admitted_cue or bool(credits) or bool(dates)
            if is_ancestor and not admitted_cue:
                continue
            if element.semantics & {"title", "fulltitle"} and not bibliographic_label:
                field, canonical = (
                    EpubInspectionField.TITLE,
                    normalize_title(value),
                )
            elif element.semantics & {"contributor"} and not any(
                credit[0] is opening.OpeningField.CONTRIBUTOR for credit in credits
            ):
                field, canonical = EpubInspectionField.CONTRIBUTOR, value
            elif byline is not None and len(byline.group(1)) <= 280:
                field, canonical, role = (
                    EpubInspectionField.CREATOR,
                    byline.group(1),
                    "author",
                )
            elif element.semantics & {"creator"}:
                field, canonical, role = EpubInspectionField.CREATOR, value, "author"
            if field is not None:
                facts.append(
                    EpubInspectionFact(
                        field,
                        report.source_fingerprint,
                        value,
                        canonical,
                        value,
                        "not_applicable",
                        "not_applicable",
                        "not_applicable",
                        "opening_matter",
                        rear_provenance=provenance,
                        role=role,
                    )
                )
            for indicia in bibliographic_indicia:
                labelled = indicia.context_span is not None
                if labelled:
                    allocation = bibliographic_label_allocations.get(
                        opening._bibliographic_indicia_label_key(
                            element,
                            indicia,
                        )
                    )
                    if (
                        allocation is None
                        or allocation.emitter_ordinal != element.ordinal
                    ):
                        continue
                try:
                    indicia_value = validate_projection_text(
                        indicia.value,
                        "rear-spine bibliographic indicia",
                        280,
                    )
                except EvidenceValueError:
                    continue
                inspection_field = {
                    opening.OpeningField.SUBTITLE: EpubInspectionField.SUBTITLE,
                    opening.OpeningField.PUBLISHER: EpubInspectionField.PUBLISHER,
                    opening.OpeningField.IMPRINT: EpubInspectionField.IMPRINT,
                }[indicia.field]
                if not labelled and any(
                    owner in element.ancestors
                    and owner_field is inspection_field
                    and owner_value == indicia_value
                    for owner, owner_field, owner_value in bibliographic_owners
                ):
                    continue
                if not labelled:
                    bibliographic_owners.append(
                        (element.ordinal, inspection_field, indicia_value)
                    )
                supporting_context = value
                if indicia.context_span is not None:
                    context_start, context_end = indicia.context_span
                    supporting_context = projection.text[
                        element.start + context_start : element.start + context_end
                    ]
                try:
                    supporting_context = validate_projection_text(
                        supporting_context,
                        "rear-spine bibliographic context",
                        280,
                    )
                except EvidenceValueError:
                    continue
                facts.append(
                    EpubInspectionFact(
                        inspection_field,
                        report.source_fingerprint,
                        indicia_value,
                        opening._normal(indicia_value, indicia.field),
                        supporting_context,
                        "not_applicable",
                        "not_applicable",
                        "not_applicable",
                        "opening_matter",
                        rear_provenance=provenance,
                    )
                )
            series = opening._series_indicia(value)
            if series is not None:
                sequence, name = series
                if sequence is not None:
                    facts.append(
                        EpubInspectionFact(
                            EpubInspectionField.SERIES_SEQUENCE,
                            report.source_fingerprint,
                            sequence,
                            sequence,
                            value,
                            "not_applicable",
                            "not_applicable",
                            "not_applicable",
                            "opening_matter",
                            rear_provenance=provenance,
                        )
                    )
                facts.append(
                    EpubInspectionFact(
                        EpubInspectionField.SERIES,
                        report.source_fingerprint,
                        name,
                        name,
                        value,
                        "not_applicable",
                        "not_applicable",
                        "not_applicable",
                        "opening_matter",
                        rear_provenance=provenance,
                    )
                )
            for credit_field, credit_value, credit_role, _applicability in credits:
                inspection_field = (
                    EpubInspectionField.CONTRIBUTOR
                    if credit_field is opening.OpeningField.CONTRIBUTOR
                    else EpubInspectionField.CONTEXTUAL_CREDIT
                )
                facts.append(
                    EpubInspectionFact(
                        inspection_field,
                        report.source_fingerprint,
                        credit_value,
                        credit_value,
                        value,
                        "not_applicable",
                        "not_applicable",
                        "not_applicable",
                        "opening_matter",
                        rear_provenance=provenance,
                        role=credit_role,
                    )
                )
            for lexical, event, precision in dates:
                try:
                    canonical, inferred_precision = normalize_date_value(lexical)
                except ValueError:
                    continue
                if inferred_precision is not precision:
                    continue
                facts.append(
                    EpubInspectionFact(
                        (
                            EpubInspectionField.PUBLICATION_DATE
                            if event is DateEvent.MANIFESTATION_PUBLICATION
                            else EpubInspectionField.DATE_EVENT
                        ),
                        report.source_fingerprint,
                        lexical,
                        canonical,
                        value,
                        "not_applicable",
                        "not_applicable",
                        "not_applicable",
                        "opening_matter",
                        rear_provenance=provenance,
                        date_event=event,
                        date_precision=precision,
                    )
                )
            owner_spans = opening._non_isbn_identifier_spans(value)
            for match in () if is_ancestor else opening._ISBN.finditer(value):
                if resource_isbns >= 4:
                    break
                if opening._isbn_owned_by_non_isbn_identifier(match, owner_spans):
                    continue
                isbn = opening._normal(match.group(0), opening.OpeningField.IDENTIFIER)
                if len(isbn) in {10, 13} and opening._isbn_valid(isbn):
                    facts.append(
                        EpubInspectionFact(
                            EpubInspectionField.ISBN,
                            report.source_fingerprint,
                            match.group(0),
                            isbn,
                            value,
                            f"valid_isbn_{len(isbn)}",
                            "unknown",
                            "unlabelled",
                            "opening_matter",
                            rear_provenance=provenance,
                        )
                    )
                    resource_isbns += 1
            if len(facts) > 16:
                raise opening._AggregateRejected(
                    report.outcome.RESOURCE_LIMIT, tuple(facts[:16])
                )
    return tuple(facts)
