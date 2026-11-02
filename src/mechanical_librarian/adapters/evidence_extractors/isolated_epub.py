"""Parent-side EPUB inspection: open and digest one book for the isolated child."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, TypedDict, cast

from mechanical_librarian.adapters.cover_artifacts import (
    CoverDecodeRejected,
    CoverDecodeTimeout,
    decode_cover,
)
from mechanical_librarian.adapters.evidence_extractors.epub_cover import (
    read_declared_cover,
)
from mechanical_librarian.adapters.evidence_extractors.local_media import (
    LocalMediaError,
    LocalMediaLimitError,
    open_local_media,
    sha256_descriptor,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    run_epub_inspector_child,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    ArtifactAuthorityError,
    InspectionResult,
    ItemInspectionError,
    MetadataReportError,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, EpubInspectionOutcome
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    RearClaimProvenance,
    claim_scope,
)

if TYPE_CHECKING:
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )


class _RearProvenanceFields(TypedDict):
    """The exact keyword shape ``EpubRearProvenance.to_data`` emits."""

    citation: str
    marker: str
    resource_identity: str
    spine_ordinal: int
    tail_slot: int


class IsolatedEpubInspector:
    def __init__(self, artifacts: CoverArtifactStore) -> None:
        self._artifacts = artifacts

    def inspect(
        self, mount: MediaMountConfiguration, relative_path: str, maximum_bytes: int
    ) -> InspectionResult:
        opened = None
        try:
            opened = open_local_media(mount.container_path, relative_path)
            digest = sha256_descriptor(
                opened.descriptor, opened.length, maximum=maximum_bytes
            )
            source = Fingerprint("sha256", SchemaVersion("local-file-v1"), digest)
            report = run_epub_inspector_child(
                opened.descriptor,
                source,
                EpubOpeningLimits(),
                logical_identity=digest,
            )
            inspection = report
            if inspection.outcome not in {
                EpubInspectionOutcome.SUCCESS,
                EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
            }:
                raise ItemInspectionError(inspection.outcome)
            claims: list[LocalClaim] = []
            for occurrence, fact in enumerate(inspection.facts, 1):
                claim = LocalClaim.create(
                    kind=ClaimKind(fact.field.value),
                    observed=fact.observed_value,
                    canonical=fact.canonical_value,
                    excerpt=fact.supporting_context,
                    applicability=fact.media,
                    basis=fact.basis,
                    checksum=fact.checksum,
                    applicability_basis=fact.classification_basis,
                    scope=(
                        ClaimScope.WORK
                        if fact.date_event
                        in {
                            DateEvent.WORK_FIRST_PUBLICATION,
                            DateEvent.CREATION,
                        }
                        else claim_scope(ClaimKind(fact.field.value))
                    ),
                    state=FieldState(fact.state),
                    role=fact.role,
                    # ``EpubRearProvenance.to_data`` emits exactly this dataclass'
                    # keyword shape, and ``__post_init__`` revalidates every value.
                    rear_provenance=None
                    if fact.rear_provenance is None
                    else RearClaimProvenance(
                        **cast("_RearProvenanceFields", fact.rear_provenance.to_data())
                    ),
                    occurrence=occurrence,
                    date_event=fact.date_event,
                    date_precision=fact.date_precision,
                )
                claims.append(claim)
            declared = read_declared_cover(opened.descriptor, digest)
            if declared.state is None:
                try:
                    decoded = decode_cover(declared.data or b"")
                    try:
                        self._artifacts.stage(
                            decoded.artifact, declared.data or b"", decoded.preview
                        )
                        covers = (
                            CoverSource(
                                declared.source_id,
                                CoverSourceKind.EPUB,
                                CoverSourceState.RETRIEVED,
                                decoded.artifact,
                            ),
                        )
                    except ArtifactAuthorityError:
                        raise
                    except MetadataReportError:
                        covers = (
                            CoverSource(
                                declared.source_id,
                                CoverSourceKind.EPUB,
                                CoverSourceState.UNAVAILABLE,
                            ),
                        )
                except CoverDecodeRejected as error:
                    covers = (
                        CoverSource(
                            declared.source_id, CoverSourceKind.EPUB, error.state
                        ),
                    )
                except CoverDecodeTimeout:
                    covers = (
                        CoverSource(
                            declared.source_id,
                            CoverSourceKind.EPUB,
                            CoverSourceState.TIMEOUT,
                            timeout_scope="attempted",
                        ),
                    )
            else:
                assert declared.state is not None
                covers = (
                    CoverSource(
                        declared.source_id,
                        CoverSourceKind.EPUB,
                        declared.state,
                        timeout_scope=(
                            "attempted"
                            if declared.state is CoverSourceState.TIMEOUT
                            else None
                        ),
                    ),
                )
            return InspectionResult(
                digest,
                tuple(claims),
                covers,
                inspection.rear_resource_limit,
                declared.recovery,
            )
        except ArtifactAuthorityError:
            raise
        except MetadataReportError:
            raise
        except LocalMediaLimitError as error:
            raise ItemInspectionError(EpubInspectionOutcome.RESOURCE_LIMIT) from error
        except LocalMediaError as error:
            raise ItemInspectionError(
                EpubInspectionOutcome.UNAVAILABLE_SOURCE
            ) from error
        except OSError as error:
            raise ItemInspectionError(
                EpubInspectionOutcome.UNAVAILABLE_SOURCE
            ) from error
        except ValueError as error:
            raise ItemInspectionError("EPUB inspection failed") from error
        finally:
            if opened is not None:
                os.close(opened.descriptor)

    def unchanged(
        self,
        mount: MediaMountConfiguration,
        relative_path: str,
        expected_digest: str,
        maximum_bytes: int,
    ) -> bool:
        opened = None
        try:
            opened = open_local_media(mount.container_path, relative_path)
            return (
                sha256_descriptor(
                    opened.descriptor, opened.length, maximum=maximum_bytes
                )
                == expected_digest
            )
        except (LocalMediaError, OSError, ValueError):
            return False
        finally:
            if opened is not None:
                os.close(opened.descriptor)
