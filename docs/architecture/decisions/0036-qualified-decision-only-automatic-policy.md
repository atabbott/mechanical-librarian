# ADR-0036: qualified decision-only automatic policy

## Status

Accepted for milestone 0.12.0.

## Decision

A clean schema starts with the built-in profile in the visible `automatic`
posture. That posture is limited to the exact F19a qualification manifest: the
qualified title/series and publication bundles, and the qualified author,
language, and publisher options. Publisher imprint is deliberately excluded.
F20 #564 later composes its independently pinned candidate with F19a: only the
exact passing series, ISBN, source-bound genre/tag, and safe-description options
join decision-only Automatic. Tags remain off by default, and every failing or
unobserved sibling stays human-routed.

An automatic result is an immutable policy disposition, never a human actor,
apply plan, target request, or media operation. The existing exact-current
shadow outcome remains its source identity, including its policy generation,
candidate, evidence, and combination identity. The existing transaction owns
the automatic/human uniqueness check, so a human/automatic race has one winner.

An automatic decision decides its field only in the policy generation it was
recorded under, the active generation of `metadata_policy_state` (#1573). A
case holds at most one automatic decision per field and generation; one from
an earlier generation stays as history and settles, holds, authorizes and
blocks nothing. An owner decision still wins in any generation.

Preview is still non-authoritative. It may be explicitly confirmed back to
automatic only after persistence rechecks the sealed option set. Confirming
requeues every complete current work to projection in the same transaction, so
each is decided again in Automatic from the outcome Preview showed, including a
work first prepared in Preview (#1573). The preparation run records that
pass under its own `automatic_confirmed` trigger, never title recovery.
Owner pause
and durable category/global suspension make would-be automatic fields ordinary
human attention; recovery is explicit.

## Consequences

This is a clean-schema change under ADR-0013. Existing database states are not
migrated. The feature adds no provider, model, apply, target, media, or write
client. Qualification corpus inputs stay private; the checked-in manifest
contains aggregate, non-identifying qualification facts only.
