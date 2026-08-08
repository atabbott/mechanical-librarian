# ADR-0036: qualified decision-only automatic policy

## Status

Accepted for milestone 0.12.0.

## Decision

A clean schema starts with the built-in profile in the visible `automatic`
posture. That posture is limited to the exact F19a qualification manifest: the
qualified title/series and publication bundles, and the qualified author,
language, and publisher options. Publisher imprint is deliberately excluded.

An automatic result is an immutable policy disposition, never a human actor,
apply plan, target request, or media operation. The existing exact-current
shadow outcome remains its source identity, including its policy generation,
candidate, evidence, and combination identity. The existing transaction owns
the automatic/human uniqueness check, so a human/automatic race has one winner.

Preview is still non-authoritative. It may be explicitly confirmed back to
automatic only after persistence rechecks the sealed option set. Owner pause
and durable category/global suspension make would-be automatic fields ordinary
human attention; recovery is explicit.

## Consequences

This is a clean-schema change under ADR-0013. Existing database states are not
migrated. The feature adds no provider, model, apply, target, media, or write
client. Qualification corpus inputs stay private; the checked-in manifest
contains aggregate, non-identifying qualification facts only.
