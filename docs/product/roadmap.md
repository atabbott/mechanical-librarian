# Roadmap

[GitHub milestones](https://github.com/atabbott/mechanical-librarian/milestones)
are the authoritative plan. Each milestone's description states its outcome,
its units in order, and what it excludes. This page summarises them and is
updated when a milestone opens, closes or is reordered.

Before 1.0.0, each numbered milestone is an internal checkpoint that publishes
nothing; see
[work management](../development/work-management.md#milestones-and-checkpoint-versioning).

## Delivered

Checkpoints 0.1.0 to 0.29.1 built the product up to its current state:

- read-only Audiobookshelf inventory, EPUB and audio evidence, and catalogue
  evidence from several providers;
- explainable field-level proposals, review cases and owner decisions;
- qualified automatic decisions, and guarded, consented, read-back-verified
  writes to Audiobookshelf;
- the authenticated owner workspace, Library (with Attention and work detail),
  Policy, Activity and Settings, and guided first run;
- audiobook matching and decisions;
- an optional AI cover reviewer and description advice;
- a workspace in plain words, built to WCAG 2.2 AA, that holds still on phones
  and at 200% text.

The [changelog](../../CHANGELOG.md) lists each user-visible change.

## In progress

- **0.29.2 Delivery reliability and hygiene.** Test, tracking and
  documentation corrections that make every later change cheaper.
- **0.29.3 Review fixes and hygiene.** Bounded fixes left over from the 0.29.x
  reviews, across Library, Activity, Settings, tests and the Codex reviewer.
- **0.30.0 Audiobook matching and editions.** Audiobooks match the edition the
  library holds, keep that match, and get better data.

## Next

- **0.31.0 Coverage.** More of the evidence already retrieved is admitted
  safely, and covers are chosen by measured rules before any model helps.
- **0.32.0 Series and structure.** One identity per series, and a read-only
  view of what each series is missing.
- **0.33.0 First library.** A first install with thousands of works shows the
  real problems first, with progress and consent that work at that scale.
- **0.34.0 Living library.** The owner is told what happened to new arrivals.
- **0.35.0 Library health findings.** Duplicate works, unreadable files and
  items the library never indexed are reported, never changed.
- **0.36.0 Standing owner instructions** and **0.36.1 Persistence health.**
- **0.37.0 Provider economy.** Fewer provider requests for the same evidence.
- **0.38.0 Owner-directed matching.** The owner can choose which provider
  record is a work's book, and unmatched works become ordinary review cases.

## 1.0.0 first public release

1.0.0 is the first published release. It needs a settled full-library
measurement on a continuous report-only deployment and the cross-cutting gates
in the Release readiness milestone.

## Later

Further library services (Calibre-compatible first), AZW3 and MOBI evidence,
embedded-file metadata writes with their own backup gates, and further AI
reviewer backends are directions, not commitments. See
[scope](scope.md) and [future reviewer backends](future-reviewer-backends.md).
Spoken opening-credit evidence from audiobooks is planned but unscheduled; see
[opening-audio evidence](opening-audio-evidence.md).
