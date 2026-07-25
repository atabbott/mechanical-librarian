# 0.9 working plan: explainable ebook proposals

Milestone 0.9.0 is the F09o vertical slice tracked by
[#70](https://github.com/atabbott/mechanical-librarian/issues/70).
[#344](https://github.com/atabbott/mechanical-librarian/issues/344) is its
independently useful pre-activation increment. It completes the immutable
evidence input before #70 activates deterministic field proposals.

## Evidence-report increment

One selected current Audiobookshelf EPUB produces or reopens one
`metadata-report-v7` document that:

- fingerprints the exact state of all 16 Audiobookshelf metadata destinations;
- keeps the existing exact ISBN-family observation when one usable family
  exists and otherwise permits one exact title-plus-complete-author lookup;
- fails closed for incomplete authors, competing or different works,
  translations, adaptations, and uncertain abridgements;
- retains only qualified work-scoped fallback evidence, including exact
  Hardcover taxonomy categories and same-work first publication;
- admits at most four provider cover sources per connection and eight overall,
  with safe provenance, content-addressed deduplication, and explicit
  truncation; and
- remains synchronous, immutable, deterministic, exact-current-schema,
  report-only, and free of proposal, review, decision, or write authority.

The installed `metadata-report` path demonstrates both ISBN and no-ISBN
observations with synthetic fixtures. `metadata-report-get --report-id`
reopens the exact persisted bytes without configuration, credentials, target
access, or network access.

## Activated ProposalSet boundary

#70 consumes only the exact completed report contract. Its
`proposal-set-v1` document is a separate content-addressed immutable artifact
with one closed result for every target destination. Creation revalidates the
external target/media/cover/artifact state, performs no provider call, and
transactionally rechecks the report digest plus current pointer before insert.
The fixed conservative policy is versioned in the artifact and introduces no
review, decision, or apply authority.

## Exclusions

This milestone adds no provider, discovery path, fuzzy search, configurable
policy, AI, batch/background/retry/cache behavior, UI/API, review admission,
decision, write, migration, compatibility parser, tag, release, or deployment.
