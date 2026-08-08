# 0033 — Persist immutable metadata-policy drafts before activation

## Status

Accepted for F09p #476.

## Context

An owner needs to inspect the built-in metadata policy, make bounded changes,
and understand each library's current impact without changing evidence,
proposals, queue authority, or any target/media metadata.

## Decision

Use a single closed, canonical profile document. Its content hash is its stable
identity. Drafts are immutable; the built-in policy remains the active profile
through this increment. Sparse per-library configuration overrides are resolved
only for preview and are never written by the application. A proposal-binding
value records an exact resolved identity without modifying an EvidenceReport.

The catalogue has no qualified automatic fields. Automatic therefore remains a
visible unavailable choice, Ask me and Ignore are manual-only, and Explicit is
unsupported. Preview reads named libraries, categories, and current unapplied
counts without creating a successor, decision, queue entry, or activation.

## Consequences

F09p #477 may consume these immutable values for an explicit transactional
cutover. It must not infer activation from draft creation or preview.
