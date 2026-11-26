# Data privacy

Mechanical Librarian keeps the owner's library metadata and the evidence
needed to explain and recover every change. All of it is private application
state under `/data`. There is no retention period, pruning, purge or selective
export, except that held provider answers past a size ceiling are removed: reports, ProposalSets, cases, decisions and Activity events are
append-only, and recovery or deletion works on whole databases. Anyone who can read the volume or a backup can read everything,
including secrets in store mode. Store `/data` and its backups as private,
owner-only state with encryption at rest
([backups and recovery](../operations/backups-and-recovery.md)).

The security invariants behind these rules are in [security](security.md).
Where each store lives is in [SQLite topology](sqlite-topology.md).

## What is retained

**Reports.** Each metadata report keeps:

- the configured connection and library names and the target item ID;
- the 16 target destinations, each bounded and with its state;
- an opaque file identity and content digest, never a host path;
- bounded local claims with their provenance, including opening-matter
  excerpts of at most 280 characters;
- per provider: bounded candidate fields, opaque provider work and edition
  identities, the closed set of rich evidence kinds, lane identities and
  normalized ISBN families, and aggregate diagnostics;
- cover outcomes with opaque identities, an allow-listed edition context,
  digests, dimensions and media type;
- for an EPUB with a malformed cover declaration, the closed fallback and
  refusal codes of its shadow cover recovery, never a reference or member
  name;
- for an audiobook, each recognised sidecar's bare file name, digest and
  closed codes, and the values recorded but not believed: Libation shadow
  values and, for a sidecar that starts with a byte-order mark, what a reading
  without the mark adds. Never a path, excerpt or association text.
- for a work its owner linked to a provider record, the link facts the
  preparation used: medium, scope, provider, the stored record identity, the
  closed outcome and reason, and the anchor title and creators read from that
  record, bound by the link's digest.

Cover originals and previews are stored once per digest in the artifact store.

**Owner links.** Each link, replacement and withdrawal is an append-only row
keyed by connection, library and item ID: the scope, source and record
identity, the record's title and at most ten creators as the owner chose it,
the acting account, the time, a request digest and the link digest. A
withdrawal keeps no record value. Rows are never updated or deleted; a clean
rebuild removes them with the rest of the store
([ADR-0104](decisions/0104-owner-identity-links.md)).

**Provider answers.** `/data/private/provider-responses.sqlite` keeps each
success body a provider returned for an observation that ended clean, so a
later preparation reuses it instead of asking again
([ADR-0101](decisions/0101-reuse-stored-provider-answers.md)). A body is kept
with a digest of its request and when it was fetched, never the request text,
a header or a credential. The bodies name the owner's books, and a request
digest can be confirmed by guessing the title it asked for, so the file is
private evidence like the reports. No report, diagnostic or support bundle
reads it, and the answers fetched longest ago are removed past 2 GiB.

**Derived records.** ProposalSets, review cases, policy-preview outcome sets
and preparation work rows hold only bounded values, opaque identities,
evidence references, hashed correlation groups and fixed codes already
derived from a report. They do not copy excerpts, provider evidence or cover
provenance. The `review_attention_facts` table and other derived browse facts
are recomputed from those rows and add no new personal data.

**Decisions and effects.** Owner and automatic decisions, cover
considerations and field-effect plans retain the values needed for
attribution and recovery: pre-values, desired values, owner-entered text,
consent, state, attempts and failure codes. Owner-entered names are kept like
owner-entered text; a clear stores no value. A work written in the last 30
days keeps its current case when nothing is left to decide, holding no more
than any case
([ADR-0105](decisions/0105-going-back-after-a-write.md)). Activity events are
append-only.

**The Library visit.** One row keeps how far Library's listing had got at the
owner's last visit and when Library was last used, as integers
([ADR-0103](decisions/0103-keep-the-library-visit-on-the-server.md)). The
time is overwritten on each use; no history is kept, logged or exported.

**Reviewer work.** A cover consideration keeps its packet, the request and
profile identities, and one locally admitted closed observation or a typed,
value-free failure. Description advice keeps each attempt and the one bounded
description the reviewer authored. The taste profile keeps each build's
state and closed failure code, the daily request count, and each admitted
profile version with its model, the counts and one digest of the inputs it
was built from, whether each rule is the model's, edited or the owner's, and
the texts the owner removed or replaced. Each taste judgement keeps its state
and closed failure code, and an admitted verdict keeps only the comparison's
own cover reference, the decision, a confidence, rule numbers and digests of
what was sent: no rule text or image.
None keeps prompts, raw model streams, reasoning or unadmitted output. A
regeneration keeps the profile, its builds and the daily request count, and
discards the judgements and verdicts with all derived state; a clean rebuild
discards everything.

**Settings and access.** In store mode the application database holds owner
settings and their secrets: the Audiobookshelf token and provider API keys.
In file mode those secrets stay in the files `config.json` names. In either
mode an OpenRouter key is kept write-only in the application database. The
owner password is held as a verifier. The session database holds only digests
of session tokens and clients. A Codex sign-in is kept in the
private `/data/reviewer/auth` directory.

## What is never retained

No store, log, diagnostic, support bundle or API response contains:

- raw Audiobookshelf or model responses, provider responses outside the
  provider-answer store above, request URLs or bodies;
- provider cover URLs, endpoint paths or arbitrary upstream properties;
- private filesystem paths (only the catalogue database keeps each supported
  item's service and mount path and its folder path in the library, and no
  report, list or API response shows one), arbitrary EPUB text or audio
  content;
- an identifiers file, its paths or its rows (its preview, held in memory for
  15 minutes, keeps each row's number and reason and the work it would link;
  an import keeps its counts and the links it made);
- an inventory beyond the bounded current catalogue;
- prior cover bytes, except in the recovery and retained directories below;
- secrets outside the settings stores above;
- the Open Library contact email anywhere except the settings and that
  provider's requests (it is also kept out of every report identity and
  fingerprint);
- reviewer job files, raw streams, reasoning or auth state outside the
  reviewer directory;
- a [Find this book](../product/find-this-book.md) search's text, results or
  cover previews. They are held only in server memory, for the session that
  searched, for 15 minutes, and a new search, logout or restart discards
  them. A preview is never written to disk or the artifact store.

Fixed stderr lines contain no rejected value
([observability](../operations/observability.md)). Diagnostic timings are off
by default and keep only stage codes and durations.

### Prior cover bytes

Before a cover write, the previous cover is kept in an owner-only private
recovery directory named by the plan digest. A persisted, retryable cleanup
obligation removes it on every terminal path: verified, stale, superseded or
abandoned. A cover upload reads only the digest-verified original and keeps
no copy. Those bytes and their path never appear in documents, APIs, logs,
diagnostics, fixtures, exports, support bundles or reviewer input.

When an automatic switch replaces a cover that was already there, that
original is also kept so it can be put back (#1363). The plan's recovery copy
must hash to the plan's own pre-value; it is bound to the plan in an
append-only record and then kept under its SHA-256 in an owner-only
`retained-covers` directory beside the recovery directory. The directory holds
at most 2,000 originals or 2 GiB. When it is full, nothing older is dropped:
a switch that would need room is suggested instead, and a write that races to
a full store stops and writes nothing. If the write ends any way
other than verified, or a newer switch pushes it off the list of the most
recent 1,000 switches, a persisted, retryable obligation removes the copy,
queued in the same transaction as that change. A retained original is never
exported, logged, offered as a download or sent to a reviewer, and its path
appears nowhere. While a copy is retained, the owner sees the replaced cover's
stored preview in Covers and on the book page and can put it back, which
uploads the retained original through the guarded write; the preview is never
cached, and the retained original is never served. Report assembly stores
only the original's preview in the artifact store, so its bytes stay in the
retained directory alone and a release leaves no copy of them. A Put back that
verifies releases the copy in the same transaction.

An original the decoder refuses is kept and put back byte for byte all the
same, because retention never decodes it (#1362). It has no preview: report
assembly offers it by the digest the retained store has just verified and
stages nothing, the book page and Covers show "Image unavailable" in its
place, and Put back uploads the exact retained bytes.

## What leaves the host

The product sends no telemetry. It contacts only:

- **Audiobookshelf**, to read items and to make guarded metadata and cover
  writes;
- **metadata providers** the owner enabled, with ISBNs, ASINs, titles and
  authors as query text, plus the configured API key or Open Library contact
  email. When Audiobookshelf names no author for an ebook, its EPUB's own
  creators are sent instead. Hardcover also receives case forms of the title
  and inverted or initials forms of an author's name, derived only from values
  already sent. For a book it found, Hardcover also receives that book's own
  id, to list its editions' covers, and its asset host receives the cover
  image requests. Neither carries an item value. iTunes Search receives an
  audiobook's title and author once, to find its cover, and only while its
  cover switch is on. Find this book sends the title and authors, ISBN or
  ASIN the owner typed to the one source the owner picked
  ([provider egress](provider-egress.md#owner-search));
- **an AI reviewer**, only if the owner selected one
  ([AI trust boundary](ai-boundary.md)). Cover review sends opaque candidate
  references, re-encoded previews, incumbent state and bounded public provider
  provenance; never a target, library, path, URL, credential, raw provider
  response or private bibliographic value. Description advice sends only
  provider-backed description texts and the three cleanup choices. The taste
  profile sends up to 100 previews of covers the owner chose and passed over,
  as JPEGs, with each cover's size, shape, same-art group and role and the
  book's format, and the rules the owner wrote or edited and the texts the
  owner removed; never a title, author, identifier, path or provider text.
  The suggestion model receives one work's current and alternative covers as
  previews with their sizes and the book's format, and the profile version's
  rules, the owner's own included; never a title or file.
  With Codex selected, those covers go to OpenAI through the owner's ChatGPT
  sign-in, as cover review's do. OpenRouter cover, taste profile and
  suggestion requests ask providers to deny data collection; description requests do not. Read the model provider's data
  policy before enabling any of them.

## What surfaces show

All owner surfaces need a session and are `private, no-store`.

- **Library, Attention and review** show the owner's own metadata and evidence.
- **Library progress** returns counts only: works per lens in total and per
  library, with each library's name, and works needing the owner per field.
  Never a title, item, path, digest or run identifier.
- **Covers → Taste profile** shows the profile's rules, versions and build
  state, the suggestion model's state as counts and a time, and how many
  suggestions the owner confirmed. Rule text is model output or the owner's,
  shown as text only, and never reaches a log, status, diagnostic or support
  bundle.
- **Covers → To decide** shows a suggested work's strength and, in its open
  work, the texts of the rules the suggestion cites, read from the profile
  version it was made under. The verdict itself still holds no rule text.
- **Covers → Switched** shows the texts of the rules a taste switch cited, read
  from the version it was made under, and its preview shows the same for the
  switches it found. An automatic taste decision keeps only the verdict's
  identifier and the version number. Activity shows that number and no rule
  text.
- **Preparation status** shows only connection and library names, an opaque
  scan identifier, a closed state, counts and whether a request was
  coalesced: never titles, file names, queries, failure text or values.
- **Activity** returns one bounded page of ledger facts: opaque identities,
  library and destination names, state, time, a report title where one is
  retained, and the before and after values the event's own document holds.
  Opaque identities such as a cover token are described, not printed. It is
  not an export or a write surface. A corrupt stored row fails closed with a
  redacted diagnostic.
- **Write plan** (Activity's permission review) shows prepared changes before
  writes are allowed: library names, work titles and authors, field names, the
  current and new values, the reason and source names. The inline plan holds
  at most 60 rows; its file at most 20,000 rows in 16 MiB, made on request
  and never stored, logged or sent anywhere. Neither carries a work,
  item or decision identity, a path, an artifact key or a cover token: a cover
  is described in words (#1336).
- **Policy preview** samples at most 50 records, truncated at 1 MiB. Draft
  impact shows current and resulting values for at most 20 works across all
  libraries and is not persisted. A cover is described in words, with no
  reference, artifact key, digest or token. The `policy-preview` command
  prints the same private projection to stdout; keep it out of public logs
  and support artifacts.
- **Support preview and export** are capped, allow-listed and re-redacted;
  databases, environment, secrets, paths, metadata, excerpts, media, covers,
  recovery bytes and retained originals are excluded.

The process also keeps validated documents in memory to serve these reads:
up to 64 MiB of Activity facts, report text for Activity bounded at 48 Mi
characters, browse documents and up to 16,384 cover currentness documents.
They are never persisted and disappear with the process
([ADR-0082](decisions/0082-runtime-activity-facts.md),
[ADR-0083](decisions/0083-validated-browse-document-reuse.md)).

## Contributor workbench

The repository-only [workbench](../development/contributor-workbench.md)
accepts synthetic fixtures, explicitly supplied test workspaces or an
explicitly approved private corpus, and refuses production workspaces and
targets. State is disposable by default; persistent `.workbench/` state is
ignored by version control and never ships. Results expose only status,
artifact identifiers, byte counts and digests. Public qualification evidence
is aggregate only: it names no work, value, identifier, path or per-case
result.
