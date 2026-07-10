# Product Vision

## Vision

New ebooks and audiobooks should arrive with correct, useful metadata and
covers without requiring routine human maintenance.

Mechanical Librarian continuously observes supported media-library services,
collects evidence, proposes field-level changes, makes decisive automated
reviews, applies permitted changes through supported APIs, verifies the result,
and retains enough provenance to explain or reverse what happened.

## Users

The initial user is a technically capable self-hoster running one personal or
household library. The product should nevertheless be installable and operable
without knowledge of its implementation or the original author's environment.

## User promise

- Installation is one container, one persistent volume, read-only media mounts,
  and one web port.
- New items remain immediately available for consumption; curation happens
  asynchronously after files stabilize.
- The normal review queue stays small.
- Automatic decisions are visible in history but do not demand routine review.
- Failures stop affected work safely and explain how to recover.
- A user can export configuration, audit history, and backups without vendor
  lock-in.

## Measures of success

- Most new items become acceptably correct without human action.
- Provider, AI, authentication, backup, or apply failures cannot silently
  corrupt production metadata.
- Reprocessing an unchanged item produces no additional production change.
- Users can understand why a field changed and which evidence supported it.
- Recovery from a failed upgrade, expired AI login, or bad apply is documented
  and testable.
- A new contributor can locate the governing contract for a behavior without
  relying on maintainer memory.
