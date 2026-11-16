# ADR-0103: Keep the owner's Library visit on the server

- Status: proposed
- Date: 2026-11-16
- Owners: project maintainers
- Issues: #1351
- Amends: ADR-0013 (one more clean rebuild)
- Supersedes: none
- Superseded by: none

## Context

Works reach Library without the owner doing anything: a polled scan lists
them. Nothing on Library says which arrived since the owner last looked, and
no arrival time is stored. The owner uses more than one device and expects
them to agree, so the answer cannot live in one browser. A cookie or browser
storage is per device. The session database is per sign-in and is emptied by
signing out. The settings document is the owner's configuration, saved whole
and compared by revision, and is the wrong place for a value that moves by
itself.

## Decision

1. **One row.** The application database gains `library_visit`, a singleton
   with seven integers: `seen_packages`, `seen_works`, `seen_reports`,
   `shown_packages`, `shown_works`, `shown_reports` and `active_at`. Each
   `shown` position is at least its `seen` position. No migration is supplied
   (decision 6).
2. **New by listing position.** A position is the last `rowid` of a table when
   Library was read: `current_review_packages` for listed works,
   `review_preparation_work` for works that could not be read, and
   `metadata_reports`. A listed work is new while its package row lies past
   `seen_packages`, or while no report for the same connection, library and
   item lies at or before `seen_reports`. The second test catches a newcomer
   whose package row reused a deleted row's position. A work that stopped
   before its report is new while its work row lies past `seen_works`. With no
   row, nothing is new.
3. **The visit rule.** A visit ends when Library has not been used for
   `VISIT_GAP_SECONDS` (1,800) on any device. `shown` is the furthest listing
   a device opened in the New lens. A read chooses its boundary from the clock
   it is given: `shown` once the gap has passed since `active_at`, otherwise
   `seen`. A read never writes, so a device opened later reads the next visit
   without being told. The first recorded use after a gap copies `shown` into
   `seen`.
4. **One guarded write.** `POST /api/v1/library/visit` records a use of
   Library. Its body is `{"listing": null}` or the three positions a rendered
   New lens answered with. A listing raises `shown`, never past the listing's
   current end; null leaves it. Either sets `active_at`. The route is an
   ordinary unsafe route: session, CSRF, Origin and build identity, a bounded
   closed body, and 204. It publishes the `library` live update only when an
   existing `seen` changed. The browser posts from Library only, while the
   page is visible: when its works first render, when a rendered New lens
   lists further, and on the owner's input once 300 seconds have passed since
   the tab's last post.
5. **The first mark.** With no row, a post records nothing until no
   preparation run is starting, running or blocked and one scan has completed.
   It then sets `seen` and `shown` to the listing's current end, whatever the
   body holds. Nothing is new before the first mark or straight after it.
6. **Clean rebuild.** The table is part of the exact schema (ADR-0013). An
   earlier database is refused before mutation.
7. **One time, no history.** `active_at` is overwritten on each recorded use.
   No earlier value is kept, no visit is counted, and the row is not logged,
   exported in a support bundle or shown on a diagnostic. A clean rebuild
   discards it.

## Consequences

### Positive

- Every device and session agrees on what is new, through restarts and
  sign-outs.
- Reads stay reads: no Library request takes a write lock.
- No arrival time, per-work flag or event row is added; the cost is one row.

### Negative

- New depends on `rowid` stability. `current_review_packages` and
  `review_preparation_work` must keep a work's row in place while it is
  listed, so neither may be rebuilt by delete and insert, and neither table
  may gain `WITHOUT ROWID` or a `VACUUM` that renumbers it.
- A work that leaves and returns counts as new. A work with a case that was
  removed after two successful discoveries without it is listed again on a
  fresh row, and a work that could not be read is given a fresh row when it
  fails again after a discovery without it.
- A work that could not finish and is later read counts as new: its first
  report gives it a package row past `seen_packages`. New lists it once and
  drops it at the next visit after New was opened.
- A newcomer that fails at once can be missed by New when the newest work row
  was deleted, because its work row can reuse a position at or before
  `seen_works`. It is still listed under Could not finish.
- A work first listed and removed between two visits is never shown as new.
  A search or a later page of New marks the whole lens as shown.
- A Library read now depends on the server's clock. A clock set back reads as
  no gap; one set forward ends the visit early.
- Library in use costs one small write every five minutes for each open tab.

### Neutral or deferred

- A first-listed time for each work, a line for each arrival, and saying which
  works a later search changed are not built.
- `review-library-v8` adds the New count and lens and the listing positions.
