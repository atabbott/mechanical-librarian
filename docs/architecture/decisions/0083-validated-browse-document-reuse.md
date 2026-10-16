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
