# ADR-0089: Search an unmatched audiobook again

- Status: accepted (cadence chosen by the owner on 2026-10-29)
- Date: 2026-10-29
- Owners: project maintainers
- Issues: #1451
- Amends: ADR-0069 (a terminal audiobook outcome is no longer permanent while
  no recording is admitted)
- Supersedes: none
- Superseded by: none

## Context

An audiobook with no ASIN of its own reaches its recording through the Audible
title-and-author search. #1451 first proposed searching a second Audible store
when the first one listed nothing inside the tight band. Two owner-approved
probes on 2026-10-28 tested that:

- The fallback store reached none of #1294's five wrong-recording items.
- A fresh search of the first store reached three of the five, with the same
  queries whose retained #1294 pages did not list them.

So those misses came from Audible's search results changing between days, not
from which store was searched. The owner dropped the second store and the
library `languages` setting, and asked instead that an item be searched again.

Nothing searched such an item again. The discovery scan keeps a finished work
whose file, source and producer fingerprints are unchanged and whose report is
still current. That covers an `evidence_only` audiobook and one that holds a
case, and it is deliberate, because preparing an audiobook re-reads and
re-hashes its audio. An unmatched audiobook therefore kept its first answer for
as long as its files did not change.

## Decision

**Which items.** An audiobook report has an admitted recording when an
audiobook authority answered with a product that is inside the tight band and
whose narrators are not disjoint from the recording's
(`recording_admitted`). This is the same admission that ADR-0088 carries
forward, read without the carrying rules. Every other audiobook report is
unmatched. Ebooks are never searched again by this rule.

**When.** Publishing an unmatched audiobook report marks its preparation work
with `research_not_before`, set to the publication time plus
`RESEARCH_INTERVAL_SECONDS` (seven days). The owner chose weekly over daily, an
owner-only button, and leaving case-holding items alone. A matched report
clears the mark. The discovery scan returns a finished work to extraction once
its mark has passed, whether it holds a case or completed `evidence_only`, and
it keeps the mark while the work runs. A changed file, a stale report or a
failed provider stage starts the work afresh, and that clears the mark.

**What a search may replace.** When a marked work publishes a report that still
admits no recording, and the item's current case was admitted from a report of
the same file, the open case stays as it was. In the same transaction:

- the current-report and report-lineage pointers return to that case's report;
- the work completes on `case` with the case's lineage;
- the work is marked for the next interval.

The new report is kept only as history. A provider failure in that search is
treated the same way: it never replaces a case. A search that admits a recording
goes on to proposal and admission like any refreshed preparation, and admission
supersedes the case. An item with no case takes the new evidence either way.

**Bounds.** A search is an ordinary preparation, so it uses the existing
dispatch bounds, pacing and provider recovery. It adds no request, store or
setting.

**Persistence.** `review_preparation_work` gains the nullable
`research_not_before` column. Under ADR-0013, every earlier application
database is refused before mutation and needs a clean rebuild.

## Consequences

### Positive

- An item Audible's search missed on one day is asked again, so a fresh listing
  can rescue it without the owner doing anything.
- The owner's review queue changes only when a search finds a recording.

### Negative

- An unmatched audiobook's audio is re-read and re-hashed about once a week.
  On #1294 that is 124 of 250 audiobooks.
- A search that keeps a case still leaves its report in the history, so the
  report history grows by one row per unmatched item per week.

### Neutral or deferred

- An item whose Audible runtime is itself off from the file, such as #1451's
  Moneyball, is not reached by searching again. #962 checks Hardcover editions
  for those.
- Searching only the provider stage on the stored local facts would avoid
  re-reading audio. It is a larger change, deferred until the re-read proves
  costly.

## Alternatives considered

### Search a second Audible store

The probes reached none of the five items through it, while a fresh first
store reached three. It also needed a new library setting.

### Re-search daily

This rescues items sooner, at about seven times the Audible traffic and audio
re-reads. The owner chose weekly.

### Leave case-holding items alone

All five wrong-recording items hold a work-level case without a recording, so
none of them would be rescued.

## Validation

`tests/unit/test_unmatched_recording_research.py` covers:

- the mark is set and cleared at publication;
- a scan before the interval queues nothing, and one after it extracts again;
- an unimproved search keeps the open case, its report and both pointers, and
  the next scan queues nothing;
- a search that admits a recording supersedes the case and clears the mark;
- an `evidence_only` item takes the new evidence;
- a changed file clears the mark;
- the composition root marks only audiobook reports `recording_admitted`
  refuses.

An offline aggregate over the retained #1294 run, re-qualified by this build,
found 250 audiobooks:

- 126 have an admitted recording and are never searched again.
- 124 are unmatched: 49 hold a case and 75 completed `evidence_only`.
- All six items behind the five wrong-recording cases are unmatched, so each is
  searched again weekly. The 2026-10-28 probe found a fresh search reaches three
  of the five.
