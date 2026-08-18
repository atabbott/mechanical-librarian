import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.persistence import cover_artifacts
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.cover_evidence import CoverArtifact


def test_stage_deduplicates_and_reads_exact_preview() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = CoverArtifactStore(Path(directory))
        artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
        store.stage(artifact, b"source", b"preview")
        store.stage(artifact, b"source", b"preview")
        store.require_artifact(artifact)
        assert b"preview" == store.preview(artifact)


def test_require_artifact_needs_the_original_and_preview() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = CoverArtifactStore(root)
        artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
        store.stage(artifact, b"source", b"preview")
        (root / artifact.original_digest).unlink()
        with pytest.raises(MetadataReportError):
            store.require_artifact(artifact)


def test_require_artifact_uses_the_cover_pipeline_bound() -> None:
    """Both sides of the original bound, which is not the preview bound.

    `require_artifact` reads the original under `_MAX_ORIGINAL_BYTES` and the
    preview under the 1 MiB preview bound, so an original between the two is
    accepted and one above the larger bound is refused. Only the acceptance was
    here before, with no assertion at all: the test passed for an original of
    any size the store happened to admit, and would have kept passing had the
    original been read under the preview bound instead.

    The evidence for strengthening it is that absence, not a surviving mutant.
    #737 measured five modules and `cover_artifacts.py` was not among them, so
    nothing here is backed by a mutation verdict.
    """
    with tempfile.TemporaryDirectory() as directory:
        original = b"x" * (1_048_576 + 1)
        store = CoverArtifactStore(Path(directory))
        artifact = CoverArtifact.create(original, b"preview", "image/png", 1, 1)
        store.stage(artifact, original, b"preview")
        store.require_artifact(artifact)
        assert b"preview" == store.preview(artifact)

        oversized = b"y" * (8 * 1024 * 1024 + 1)
        refused = CoverArtifact.create(oversized, b"preview", "image/png", 1, 1)
        store.stage(refused, oversized, b"preview")
        with pytest.raises(MetadataReportError):
            store.require_artifact(refused)


def test_digest_mismatch_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = CoverArtifactStore(Path(directory))
        artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
        (Path(directory) / artifact.preview_digest).write_bytes(b"wrong")
        with pytest.raises(MetadataReportError):
            store.preview(artifact)


def test_recovery_preserves_referenced_history_and_removes_staged_orphans() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = CoverArtifactStore(root)
        retained = CoverArtifact.create(
            b"retained source", b"retained preview", "image/png", 1, 1
        )
        orphan = CoverArtifact.create(
            b"orphan source", b"orphan preview", "image/png", 1, 1
        )
        store.stage(retained, b"retained source", b"retained preview")
        store.stage(orphan, b"orphan source", b"orphan preview")
        store.recover_unreferenced(
            lambda digest: (
                digest
                in {
                    retained.original_digest,
                    retained.preview_digest,
                }
            )
        )
        assert store.preview(retained) == b"retained preview"
        assert not (root / orphan.original_digest).exists()
        assert not (root / orphan.preview_digest).exists()


def test_recovery_keeps_references_without_reading_or_hashing_them() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = CoverArtifactStore(root)
        orphan = CoverArtifact.create(
            b"orphan source", b"orphan preview", "image/png", 1, 1
        )
        store.stage(orphan, b"orphan source", b"orphan preview")
        # The recovery sweep must decide from directory entries alone. Forcing
        # the failure through the module's own read and hash primitives, rather
        # than through a private member of the store, keeps the proof attached
        # to the syscall and the digest rather than to one method name: it
        # still fails if `_read` is renamed, inlined, or moved onto a
        # collaborator.
        with (
            patch.object(
                cover_artifacts.os,
                "read",
                side_effect=AssertionError("retained blob read"),
            ),
            patch.object(
                cover_artifacts.hashlib,
                "sha256",
                side_effect=AssertionError("retained blob hashed"),
            ),
        ):
            store.recover_unreferenced(lambda _digest: True)
        assert (root / orphan.original_digest).is_file()
        assert (root / orphan.preview_digest).is_file()


def test_same_artifact_identity_with_different_bytes_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = CoverArtifactStore(Path(directory))
        artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
        store.stage(artifact, b"source", b"preview")
        with pytest.raises(MetadataReportError):
            store.stage(artifact, b"different source", b"preview")
