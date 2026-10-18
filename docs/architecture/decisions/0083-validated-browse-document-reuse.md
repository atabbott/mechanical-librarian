# ADR-0083: Reuse validated browse documents across requests by their exact bytes

- Status: accepted
- Date: 2026-10-16
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Library, search, the attention lens and Attention re-parse and re-validate the
same immutable case and report documents on every request. The #1162
re-baseline measured them at a large share of each page at 2,300 works and at
8k decisions (#1226). Reuse so far has been scoped to one operation: a read
reuses what it validated only within that read (#1202, #1215), and Activity
reuses parsers only while it constructs or reprojects its facts (#1218,
ADR-0082). Each request therefore repeats the parse of documents that cannot
have changed while their bytes are the same.

## Decision

The process-lifetime runtime activation owns one bounded cache of validated
browse objects, `ValidatedDocumentCache`, and passes it to every generation it
composes, so it outlives Settings cutovers. A standalone composition holds its
own. It holds three kinds: a canonical `ReviewCase` keyed by its case document;
a row's case and report browse facts keyed by both documents; and a case's
decision scope keyed by its case document.

- **Exact keys.** An entry is found only for the exact document strings it was
  validated from. Those strings determine the result: a case's identity is the
  SHA-256 of its bytes and the scope's other input is parsed from the same
  bytes, and the parsers are pure domain functions with no clock, environment or
  I/O. A report's identity is a content address of its parsed content, not of
  its bytes, and a case only claims it, so the pair is keyed by both documents.
- **No refusal is retained.** Only a parse that returned is stored. Bytes that
  refuse miss every time, run the original code, and refuse with the same error,
  message and cause.
- **No shared mutable state.** A stored object is deeply frozen, and each reader
  receives a detached copy of its JSON content as the plain `dict` and `list`
  values that its callers' exact-type checks require. A `ReviewCase` already
  stores its content frozen and hands out copies. Owner decisions are never
  cached: each one is still parsed and validated in the retained scope.
- **Bound and eviction.** At most 512 entries and 8 MiB of key document bytes,
  measured as UTF-8. The least recently used entry is evicted first, and an entry
  whose own documents exceed the byte bound is never held. Measured retention,
  documents included, is 2.4 to 3.8 times the document bytes, so a full cache
  holds roughly 20 to 30 MiB.
- **Concurrency.** One lock guards only lookup, insertion and eviction; parsing
  and copying happen outside it. Concurrent misses for the same bytes may each
  parse, and the first to finish is kept; both values are identical.

Nothing is persisted. Schema, startup admission, journal mode and the canonical
writers are unchanged. Detail and every write path still parse canonically.

## Consequences

### Positive

- A warm Library, search, lens or Attention page skips the case, report and
  scope parses for every row whose documents are retained.

### Negative

- The process holds up to the bound in memory for as long as it runs.
- The first request for each document is no faster, and neither is a page whose
  rows were evicted.
- The effective working set is small: roughly 170 decided to 360 undecided
  works, about 3 to 7 pages of 50. Paging cyclically through more than that
  evicts each page before it is revisited, so it gets no reuse.

### Neutral or deferred

- Decision documents and work detail still parse on every request.

## Alternatives considered

### Keep reuse per request

#1202 and #1215 already reuse within a read. That cannot help the next request,
which is where the measured cost lies.

### Key by content identity alone

Keying the row's pair by its case alone, or by the report identity the case
claims, would return the facts for other report bytes. The uncached reader
refuses such a report. Only the case identity is the address of its bytes.

### Memoise the domain parsers

A module-level `functools.lru_cache` is global mutable state. It hands every
caller the same mutable object and bounds entries, not bytes. It would also
reach writers and detail, where canonical parsing is the contract.

### Cache whole pages or persist parsed facts

Pages depend on decisions, labels and policy, which change without the documents
changing. Persisted facts are a schema change and a non-goal.

## Validation

The cache's tests compare Library, the attention lens, search, read-ahead and
Attention with the uncached code, page for page and refusal for refusal. They
run with the cache cold, warm, evicted at each bound and shared between threads.
They cover every pinned decision refusal from #1215, and case and report bytes
changed in place, including report content changed under its claimed identity.
They also check that stored objects are deeply frozen and that a caller's copy
matches what parsing returns, type for type. Mutants that key the pair by the
case alone, retain a refusal as a success, share mutable objects, or drop
eviction are each killed. The composition test holds generation wiring to the
activation's one cache.

## Amendment: cover currentness facts (#1237)

`ml_cover_consideration_current` re-validated each row's currentness document on
every call of every browse statement: about 19 ms of a 28.6 ms Library
statement and of a 38 ms Attention population statement at 8,000 decisions on
SQLite 3.40.1, held inside root admission. Its answer has these inputs:

- **The currentness document.** What it validates as (connection, library, item,
  report, whether it was prepared while fetching, its replay reference) is a
  pure function of its bytes; considerations are append-only, so other content
  arrives as other bytes. This is the only part reused.
- **Provider fetching.** The browse observation's captured identities, or the
  provider recovery store when no observation is installed. It changes with no
  revision in this database, so it is read on every call.
- **The replay reference.** A statement parameter, bound per read from the
  observation or the generation's authority; compared on every call.
- **Whether an observation is installed.** Read on every call.

Settings, the cover policy, suspensions, decisions, the clock and the database
revision are not inputs: the statements test those in SQL.

A second `ValidatedDocumentCache` holds the validated facts by the exact
document string: 16,384 entries and 8 MiB of key bytes, least recently used
first. A document that does not validate, or a call that fails, is never
retained and answers 0 as before. The store activation owns it across Settings
cutovers, since nothing in it depends on one; every other composition holds its
own. The browse cache's bound is unchanged.

Results, ordering and refusals are unchanged. Measured on SQLite 3.40.1,
interleaved with 96deda67 over two rounds, at 2,300 works and again after 8,000
owner decisions:

- The function's share of a statement, the statement with and without its
  call: Library 16.9 to 7.4 ms and Attention 18.2 to 12.3 ms at 8,000
  decisions; Library 4.1 to 1.6 ms and Attention 19.4 to 5.4 ms at 2,300 works.
  What remains is the call itself, once per candidate row.
- Serial p50: Library, search and the lens 27-29 to 25 ms and Attention 55-57
  to 45 ms at 2,300 works; 54-56 to 44-46 ms and 51-53 to 41-42 ms at 8,000
  decisions.
- With five readers and the writer at 8,000 decisions, p50 (p95): Library
  352-354 (537-1,029) to 292 (404-502) ms, Attention 350-354 (641-1,035) to 293
  (470-504) ms, the lens 201-225 (258-640) to 176-185 (263-584) ms and search
  141-198 (223-280) to 150-161 (223-306) ms.
- With five readers and the writer at 2,300 works, p50: Library 171-174 to
  157-167 ms, search 136-144 to 124-142 ms and Attention 175-177 to 160-168 ms;
  the lens is flat, 104-108 against 98-113 ms. One of the candidate's two rounds
  had a p95 tail of 358-383 ms on Library, search and Attention against
  194-218 ms before, and 162-190 ms in its other round; the stalls spanned the
  whole round, and the review judged them noise.
- Root admission stays 98-99% occupied by the writer and readers; a Library
  read holds it for 63 ms instead of 93 ms at the p50 with 8,000 decisions, and
  33 ms instead of 39 ms at 2,300 works.
- The memo held 2,299 entries, one for each current consideration, in 2.4 MiB,
  about 1.07 KiB an entry, so about 17 MiB when full. Steady resident memory
  moved within run-to-run noise: 350-379 MiB before and 355-377 MiB after at
  2,300 works, 435-477 and 438-479 MiB at 8,000 decisions.
