# Roadmap

[GitHub milestones](https://github.com/atabbott/mechanical-librarian/milestones)
hold the plan. Where this page and a milestone differ, the milestone is right.
Each milestone's description states its outcome, its units in order and what it
leaves out. This page summarises them. It changes when a milestone opens or is
reordered, and in each milestone's last pull request.

Before 1.0.0, each numbered milestone is an internal checkpoint that publishes
nothing. See
[work management](../development/work-management.md#milestones-and-checkpoint-versioning).

## Delivered

Checkpoints 0.1.0 to 0.29.2 built the product up to its current state:

- A read-only Audiobookshelf inventory, EPUB and audio evidence, and catalogue
  evidence from eight sources.
- Proposals for each field with the reasons behind them, review cases, and your
  decisions.
- Automatic decisions for the options that a qualification record names.
- Writes to Audiobookshelf that need your write permission and pass every
  check. Mechanical Librarian reads each one back.
- The workspace you sign in to: Library (with Attention and work detail),
  Policy, Activity and Settings, and a guided first run.
- Audiobook matching and decisions.
- An optional AI cover reviewer and description advice.
- Plain words in the workspace, which is built to WCAG 2.2 AA and holds
  still on phones and at 200% text.

The [changelog](../../CHANGELOG.md) lists each change you can see.

## In progress

- 0.29.3 Review fixes and hygiene. Fixes left over from the 0.29.x reviews,
  across Library, Activity, Settings, tests and the Codex reviewer.
- 0.30.0 Audiobook matching and editions. Each audiobook matches the edition
  your library holds and keeps that match. It also gets better data.
- 0.31.0 Coverage. Mechanical Librarian uses more of the evidence it already
  fetches, and each loosened rule has a measured error rate. Measured rules
  choose covers before any AI model helps.
- 0.36.1 Persistence health. The database stays fast at library scale, and
  its size stops growing with every source lookup. A restored or upgraded
  install starts safely, or says why it stopped.

## Next

- 0.32.0 Series and structure. Each series gets one identity, and a read-only
  view shows what each series is missing.
- 0.33.0 First library. On a first install with thousands of works, Mechanical
  Librarian shows the real problems first. You can follow progress through the
  backlog and check what write permission covers at that scale. Ebooks held
  only as AZW3 or MOBI files become works.
- 0.34.0 Living library. When works arrive after the first install, Mechanical
  Librarian tells you what happened to them.
- 0.35.0 Library health findings. Mechanical Librarian reports duplicate works,
  unreadable files and files the library never indexed. It changes none of
  them.
- 0.36.0 Standing owner instructions. You can stop new proposals for one field
  of one work. You can also keep a genre list for each library, and Mechanical
  Librarian maps each source's genres onto it.
- 0.37.0 Provider economy. Mechanical Librarian reuses answers it already has,
  so it asks each source less often for the same evidence.
- 0.38.0 Owner-directed matching. From a work in Library, you can search one
  source and choose which of its books the work is. After one search, a work
  that Library lists as Not matched becomes an ordinary review case.
- 0.39.0 Owner trust. You can correct what Mechanical Librarian wrote without
  waiting. You can also change policy or access and know what the change will
  do.

## 1.0.0 first public release

1.0.0 needs a measurement of a full library once every queued source lookup has
finished. The measurement comes from a report-only deployment that runs
continuously. 1.0.0 also needs the work in the Release readiness milestone.

## Later

These directions have no commitment yet:

- Other library services, starting with a Calibre-compatible one.
- Writing metadata into media files, behind backup checks of its own.
- More AI reviewer backends.

See [scope](scope.md) and
[future reviewer backends](future-reviewer-backends.md). Evidence from spoken
opening credits in audiobooks is planned but not scheduled
([opening-audio evidence](opening-audio-evidence.md)).
