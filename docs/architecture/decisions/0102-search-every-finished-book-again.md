# ADR-0102: Search every finished book again

- Status: proposed
- Date: 2026-11-15
- Owners: project maintainers
- Issues: #1916, #747
- Amends: ADR-0089 (which works are searched again, and what a search may
  replace) and ADR-0101 decision 5 (a third thing that asks live)
- Supersedes: none
- Superseded by: none

## Context

A finished work keeps the answers it was first prepared with for as long as
its file, source and producer fingerprints are unchanged. Only an audiobook
that admitted no recording is searched again, weekly (ADR-0089), and a held
provider answer is reused whatever its age (ADR-0101 decision 5). Sources keep
gaining data, so the owner asked that every book be searched again after a set
period, and that the pace follow the size of the library rather than a fixed
daily limit: as many as needed to search everything every 90 days.

## Decision

1. **Period.** `REFRESH_INTERVAL_SECONDS` is 90 days. `RESEARCH_INTERVAL_SECONDS`
   stays seven days and is also the least time any finished work waits before
   it is searched again.
2. **Slot.** A work's phase is the last sixteen hex digits of its work id
   modulo the period. `next_research_slot(work_id, t)` is the first time
   strictly after `t` congruent to that phase: `t + 1 + ((phase - t - 1) mod
   period)`, always within `(t, t + period]`. Work ids are SHA-256 digests, so
   phases spread evenly and about N/90 of N finished works come due each day,
   whatever N is. Nothing counts or limits them, and no state is added.
3. **Mark.** The mark is ADR-0089's `research_not_before`, written in the
   publication transaction:
   - a matched audiobook or an ebook, prepared or kept: its slot after
     publication plus seven days, so between 7 and 97 days later;
   - an audiobook that admitted no recording: the earlier of a week on and its
     next slot, so its weekly search goes on and its slot search happens once a
     period;
   - a search kept because it failed: a week on;
   - a terminal provider failure: none; a transient one leaves the mark as it
     was.
   A provider-retry successor takes its mark by the same rule. A changed file,
   a stale report, a requested refresh, a failed provider stage or a scan
   reset by `recover_review_pipeline` after its library id or producer
   changed still starts the work afresh and clears the mark. A work finished
   before this decision has no mark; the next scan that keeps it complete
   gives it its slot after the scan plus seven days, with no backfill.
4. **Live ask.** A due work returns to extraction and is claimed holding a
   mark that has passed, so every provider asks live and replaces what is
   held, as ADR-0101 already does for ADR-0089's search. Pacing, quotas and
   provider recovery are unchanged.
5. **What a search may replace.** Only a claim whose mark had passed by its
   publication is considered, and only while the item's current package names
   a report of the same file that the publication just replaced. A package
   that names a proposal set or case must also agree with the item's case
   lineage; an evidence-only package names neither, so a lineage row left
   beside it does not matter.
   Everything stays — the case, its decisions, both report pointers and the
   work's outcome — and the new report is history only, when any of these
   holds:
   - **Same proposals.** The new report, evaluated as the proposal stage would
     evaluate it (under the library's bound policy, or with none bound under
     the built-in policy), addresses the same proposal set when addressed over
     the prior report. The report id and access times never take part. An
     evaluation that cannot be made counts as a change.
   - **Failed search.** In the new report a provider could not answer: a
     provider or lane status is one provider recovery retries
     (`PROVIDER_RECOVERY_ELIGIBLE_STATUSES`: a later ask may answer
     differently), and the prior report had none. Any other status, such as a
     candidate limit, is an answer, and the other rules decide.
   - **Weekly search.** A weekly search, one before which no slot time has
     passed since the previous publication (`next_research_slot(work_id,
     mark - week) > completed_at`), of an audiobook that again admits no
     recording, while a case is open: ADR-0089's rule, unchanged. Once a slot
     time has passed, the search is that period's slot search even if a weekly
     mark fired it. The retry a week after a failed search is a weekly search
     in this sense, so for an audiobook that then admits no recording this
     rule applies until its next slot.
   - **Nothing identified.** A search that now identifies nothing (its report
     admits no work identity) never takes a case away, whatever fired it. A
     search that stops listing a book is a more likely cause than the book
     having no match, and letting a match go is #1353's subject.
   Otherwise the ordinary path runs: proposal, admission and supersession as
   after any change. Holds carry as ADR-0085 says; decisions do not.
6. **Out of scope.** Looking a matched record up by identity when a search
   stops listing it (#1353), per-field freshness and evidence-diffed
   re-proposals (the rest of #747), any setting, a provider-only search that
   does not re-read the files (ADR-0089 defers it), and carrying decisions
   across a changed case (#1346).

## Consequences

### Positive

- Every finished book meets new source data within 97 days of being
  prepared, at a steady pace that grows with the library.
- The owner's review queue changes only when the new answers would propose
  something different.
- No schema, report-schema, policy-version or API change.

### Negative

- Each search re-reads its book's files and asks every source live, about
  four times a year per book.
- Each search keeps its report as history, whether or not it changes
  anything: about four reports per book a year. At roughly 50 KB a report
  that is about 0.2 GB a year for each 1,000 books, the same order as
  ADR-0089's weekly searches. No rule removes superseded reports yet; that is
  separate work.
- If a source answers identical asks with different values that reach
  proposals, that book gets a new case every 90 days.
- Comparing proposals evaluates the new report inside the publication
  transaction, about 4 to 12 ms on 10 to 23 KB test reports, once per due work.
- A kept work that had a pending policy-transition item has that item closed
  as stale, as the ordinary path does when the work's report changes; the
  transition does not wait for a preparation that will not come.

### Neutral or deferred

- After downtime every overdue work is queued by the next scan and claimed one
  at a time; each one's next mark returns to its own phase.
- Preparing a whole library at once, as after a clean rebuild, spreads its
  first searches over days 7 to 97.

## Alternatives considered

### A fixed daily limit

Twenty-five books a day cannot search a 5,000-book library every 90 days. The
owner asked for the pace to follow the library.

### A counter or queue table

A per-work slot derived from the work id needs no new column, table or clean
rebuild.

### Compare proposal set ids directly

A proposal set is addressed over its report id, and a live ask always changes
the report id through each provider's access time, so every search would look
changed.

### Let a search that identifies nothing take the case away

Such a search most often means a source stopped listing the book (#1316).
Taking the case away would retire it and close the writes waiting on it, as
an ordinary re-preparation that identifies nothing does (#1953), for a book
nothing has changed. Letting a match go is #1353's subject.

## Validation

- `tests/unit/test_unmatched_recording_research.py`: the slot arithmetic and
  its even spread; a matched work's mark and its slot search asking live; the
  legacy mark at the next scan; a search with the same proposals, with
  different ones, that failed over a complete or an incomplete report, and
  that identifies nothing over a case or over no case; an unmatched
  audiobook's weekly and slot searches, including a weekly mark that fires
  after the slot; a failed search over complete evidence, with or without a
  leftover lineage; a candidate limit counted as an answer; a recovery reset
  and an unfired mark that take the ordinary path; the policy seam, unbound
  and unreadable; the backlog after downtime; provider-retry successors.
- `tests/integration/test_preparation_waiting_writes.py`: under a bound policy
  with destinations, a search with the same proposals keeps the owner's
  decision, Automatic's decisions and a waiting write, which is still written
  once; a changed search supersedes the case, closes the waiting write and
  carries the hold exactly as a requested refresh does, and meets a write in
  flight as a refresh does.
