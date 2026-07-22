# ADR 0011: Isolated EPUB inspector owns its package model

## Status

Accepted

## Decision

The EPUB adapter owns package declarations, observations, citations, and usage
used to select opening resources. This model is private to the child and is
not a generic extraction port or a serialized parent protocol. One child opens
one archive once, passes that session to opening extraction, and returns only
the typed opening result.

The live title report remains the sole public consumer. Retired package/opening
diagnostics, generic local-evidence contract, controller alias, and qualifier
registry are removed.

## Consequences

Hostile archive and source-stability controls remain. A future format must earn
any shared seam separately. Persisted `local_evidence_id` remains storage-only.
