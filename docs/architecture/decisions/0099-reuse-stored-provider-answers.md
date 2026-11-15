# ADR-0099: Reuse stored provider answers, with live fallback

- Status: proposed
- Date: 2026-11-15
- Owners: project maintainers
- Issues: #1064
- Supersedes: none
- Superseded by: none

## Context

Every preparation asked every enabled metadata source again, even for a book
whose file and evidence had not changed. Each item costs several paced and
rate-limited dispatches per source, and a regeneration of derived state
repeats that whole pass.
When providers answer differently between runs, decisions built on the
earlier evidence no longer carry: a prototype of the upgrade-safe state design
found about 15% of review questions change when provider evidence changes.
Stable reuse of an answer already fetched is a precondition for decisions to
survive a regeneration, and it is most of the cost of a repeat preparation.

All eight metadata adapters already dispatch through one bounded session
(`adapters/providers/sdk.py`), which owns the dispatch, retry and byte
ceilings, the ledger, attempt records and the provider access time. A report
is content-addressed and includes each provider's `accessed_at`, so a reused
answer must bring back the time it was fetched for the report to be the same.

## Decision

1. **Seam.** The bounded session asks a provider-response store before it
   paces or sends a request. A held answer is served as the live 200 it
   replaces: the deadline check, dispatch and retry counts, ledger, byte
   budget and the current per-response bound apply as they would live, and
   the body goes through the same decoder and adapter parser. Only the pacer
   wait and the network are skipped. The attempt record keeps the live
   outcome and is marked `reused`; provider recovery's physical request
   counts leave reused records out. A session whose first answer is reused
   reports that answer's fetch time as its access time.
2. **Key.** `(adapter kind, SHA-256 of the canonical method, scheme, host,
   port, path, sorted query items, body and the adapter's revision)`. Headers
   never enter the key, so a credential sent in a header never does. Each
   adapter has a revision, raised when it starts reading an answer
   differently enough that a held one would mislead it.
3. **What is stored.** Only a 200 body the session accepted and `read`
   decoded, compressed, with its SHA-256, length and the wall time of its
   live dispatch in integer microseconds. No request text, header or
   credential.
4. **Commit.** Answers are held per provider observation and kept only when
   the observation's status and every lane's status are `candidates`,
   `no_match` or `insufficient_input`. A failed, incomplete, malformed,
   oversized, limit-exceeded or provider-declared-error lane keeps nothing
   from that observation. An answer the adapter refuses after the session
   returned it (`mark_response_attempt`) is dropped even when its lane ends
   clean, as in Hardcover's narrowing retry and edition fan-out. An adapter
   exception keeps nothing. The commit point is one wrapper in adapters
   around the adapter's `observe`.
5. **Freshness.** A held answer is reused whatever its age (the owner's
   choice, "until something asks again"). The weekly re-search of an
   unmatched audiobook (ADR-0089) asks every provider live and replaces what
   is held. Provider recovery needs no flag, since a failed answer was never
   stored. A maximum age, if wanted later, is one constant and one comparison
   at read.
6. **Store.** `/data/private/provider-responses.sqlite`, one `STRICT`,
   `WITHOUT ROWID` table keyed by adapter and digest. It follows the private
   stores' rules (absent or exact, owner-only `0600`, no-follow open, delete
   journal, `BEGIN IMMEDIATE`, no network inside a write) and is safe to
   share between processes, with a 1,000 ms busy timeout. Open runs no
   integrity check, which would read every payload; each row is inflated
   within its stated length and its digest compared when read, and a row
   that fails is deleted and is a miss. Nothing is written on a hit.
7. **Bound.** At most 2 GiB of stored payload bytes; after each commit the
   answers fetched longest ago are removed until the store is under the
   ceiling. A covering index on fetch time and size serves the sum and the
   removal without reading payloads.
8. **Failure.** A store that cannot be opened or used never fails startup or
   a preparation: requests go live, nothing is kept, and one value-free
   `provider_response_store` event names the fault. A refused file is not
   changed. The contributor workbench report and batch commands get no store.
9. **All eight sources.** Google Books answers are stored and reused like
   the others, by the owner's choice. Google's API terms appear to limit
   cached copies to what Google's cache headers allow; that wording was not
   confirmed from Google's own page.

## Consequences

### Positive

- An unchanged item prepared again sends no provider request and yields the
  byte-identical report, so its publish, proposal and admission reselect what
  is current.
- A regeneration of derived state, or a rebuild that keeps
  `/data/private`, no longer repeats the whole provider pass.
- No report, application schema or numbered version changes; losing the
  store costs only requests.

### Negative

- Answers are reused indefinitely; a source's later correction reaches a book
  only through the weekly re-search, a failed lookup's retry or the store
  being removed.
- The file reveals library contents: answers name the owner's books, and a
  request digest can be confirmed by guessing a title. It is private evidence
  of the same class as reports, kept out of every report, diagnostic and
  support bundle.
- A partly failed lookup keeps nothing, so its clean lanes are asked again
  next time.
- A hit skips Google Books' burst hold. No request is sent, so the hold has
  nothing to protect.
- Two connections to the same source share answers. That is acceptable for
  public catalogue data.
- There is no switch to turn reuse off; removing the file is the only reset.

### Neutral or deferred

- The cold-start report's `dispatches` counts reused answers as dispatches.
- Cover image bytes, reviewer observations and a freshness schedule (#747)
  are out of scope.

## Alternatives considered

### A new attempt outcome for a reused answer

Hardcover's refusal path rewrites only a `response_received` record, so a
distinct outcome would either escape the refusal, changing the report's
diagnostics against live, or be overwritten and counted as physical. A flag
beside an unchanged outcome keeps both right.

### Committing every accepted answer at once

Storing each 200 as it arrives would keep answers from a lookup whose other
lanes failed and answers an adapter later refused, which reuse would then
repeat forever. One commit point per observation stores only what a clean
observation used.

### Leaving Google Books out

Recommended for the terms risk above; the owner chose to store all eight.

### A 512 MiB ceiling

An estimate puts a 10,000-item library near 512 MiB, so that ceiling would
evict a full library's oldest answers and defeat reuse.

## Validation

- `tests/unit/test_provider_response_reuse.py`: a hit skips the pacer and the
  network; a reused observation equals the live one in its observation,
  access time, diagnostics and attempt outcomes; bounds and the deadline
  apply on a hit; unclean, refused, declared-error and adapter-failure cases
  keep nothing; a re-search asks live and replaces; each varied request fact
  is in the key; the Google Books key reaches neither the digest input nor
  the store file.
- `tests/unit/test_provider_response_store.py`: admission, refusal without
  mutation, per-row integrity, degradation events, the ceiling and its
  covering index, no write on a hit, and two processes writing at once.
- `tests/unit/test_reused_answer_preparation.py`: one item prepared twice
  through the real pipeline from the same answers converges on the same
  report, proposal and case; a different access time names a different
  report.
- `tests/unit/test_provider_recovery_evidence.py` and
  `tests/unit/test_provider_retry_successor.py`: reused records are not
  physical requests.
