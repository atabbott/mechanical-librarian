# ADR-0055: Pause review preparation by target connection

## Status

Accepted.

## Context

One Audiobookshelf connection can serve several prepared libraries. TLS,
authentication, authorization, and transport failures are connection conditions,
not item or metadata-provider failures. Retrying each work independently would
needlessly repeat a broken target call and could discard valid prior checkpoints.

## Decision

Persist one exact-current, value-free condition per configured target connection.
It blocks claims only for that connection while retaining queued work at its
first uncommitted stage and all immutable report, proposal, and package state.
An authenticated CSRF-bound owner action owns one epoch-checked, bounded TLS
probe. A successful probe marks the condition recovered and wakes the existing
supervisor; a failed or interrupted probe remains safely paused. Each scan also
persists a nullable qualification epoch: discovery performs its adapter-owned
probe exactly once before enumeration, records success before dispatch, and
resets that marker only for a later scan cycle. Recovery authorizes retained
held scans with its successful epoch, so they resume without a redundant probe.

## Consequences

Unrelated connections continue preparation and completed packages stay visible.
The operational record contains only a closed condition code, state, epoch, and
counts. Pre-release clean-schema policy applies: absent, prior, extra, or
corrupt database state fails closed rather than migrating existing data.
