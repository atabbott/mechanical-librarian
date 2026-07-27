# Metadata report

The current runtime retains one synchronous single-item report operation
alongside the 0.9 catalogue, foreground batch, and ProposalSet surfaces:

```text
mechanical-librarian metadata-report \
  --config /run/secrets/mechanical-librarian.json \
  --database /data/reports.sqlite \
  --artifact-store /data/cover-artifacts \
  --connection primary \
  --library ebooks \
  --item ITEM_ID \
  --file-index 0
```

Every option is required. There are no default paths, current-directory lookup,
implicit filenames, environment fallbacks, presentation flags, or alternate
output formats.

An exact persisted report can be reopened without operational configuration,
credentials, target access, provider construction, or network access:

```text
mechanical-librarian metadata-report-get \
  --database /data/reports.sqlite \
  --report-id report_<64-lowercase-hex-characters>
```

The current report for a known scope can also be reopened offline:

```text
mechanical-librarian metadata-report-get --database /data/reports.sqlite --current \
  --connection primary --library ebooks --item ITEM_ID --file-identity <64-lowercase-hex>
```

On success stdout is exactly the persisted canonical JSON report followed by
one newline. On failure stdout is empty, stderr contains one fixed bounded
diagnostic, and the exit status is nonzero. Diagnostics never include paths,
credentials, target values, provider bodies, or EPUB text.

Cover evidence is report-only. Where valid cover bytes are available, the report
contains a source outcome and a private artifact reference. The only supported
offline read is a report-bound preview:

```text
mechanical-librarian metadata-report-cover-preview \
  --database /data/reports.sqlite --artifact-store /data/cover-artifacts \
  --report-id report_<64-lowercase-hex-characters> \
  --artifact-id cover_<64-lowercase-hex-characters> \
  --preview-variant preview-320x480-rgba-png-v1
```

It writes raw preview bytes only to non-interactive stdout. It never performs
target, provider, or remote-cover transport and it cannot retrieve originals.

An exact current report can be evaluated by the separate fixed-policy
[`proposal-set-create`](proposal-set.md) operation. That operation references
this evidence without changing or duplicating the report and performs no
provider call.

## Preconditions

- The database path is absent for the first invocation or already contains the
  exact current schema required by the installed increment.
- `metadata-report-v1` through `metadata-report-v7`, and all earlier development
  schemas are rejected before mutation. There is no migration, import,
  preservation, or compatibility parser. Keep an old database offline if
  required, initialize a clean database, and rerun the supported report command.
  This database procedure is the narrower current-runtime application of
  ADR-0013's repository-wide pre-release clean-schema rule.
- On supported Linux deployments, the database parent directory is owned by
  the process user and grants no group or other permissions. The configured
  database basename has no `-journal`, `-wal`, or `-shm` recovery sidecar.
- The artifact-store path already exists as a directory owned by the process
  user and grants no group or other permissions. The command does not create
  this directory.
- Every absolute parent-path component is a real directory rather than a
  symbolic link. Its directory entry is protected by root/process ownership
  and non-writable permissions, or by sticky-directory ownership.
- The container runs under a dedicated Unix identity with no untrusted
  same-identity processes. Privileged and hostile same-identity filesystem
  mutation is outside the supported unprivileged exclusion boundary.
- The configuration uses `config-v4` and names the requested connection and
  library explicitly.
- The target secret and each enabled referenced provider secret are owner-only
  regular files.
- Ebook libraries map to a read-only media mount.
- The selected item is an ebook and the selected file is EPUB.

Audiobook and non-EPUB targets fail before EPUB inspection, provider
construction, provider credential access, egress, or report publication.

Linux production retains the no-follow root-to-parent chain and opens the
database through a private verified hard-link alias while holding the
parent-directory lock for the repository lifetime. It rewalks the current
absolute chain before publication writes and immediately before commit. The
alias and its clean private journal artifacts are removed on close. The lock
coordinates repository instances; ancestor permissions and the dedicated Unix
identity provide exclusion from other unprivileged actors. Non-Linux runs
retain the database no-follow and inode checks for development qualification,
but do not claim the Linux production exclusion guarantee.

## Evidence and replay

The command reads the current target item directly; it does not persist an
inventory. The target projection contains exactly title, subtitle, authors,
narrators, series, genres, publishedYear, publishedDate, publisher,
description, isbn, asin, language, explicit, tags, and cover. Each destination
preserves one exact `value`, `missing`, `null`, `empty`, `unloaded`,
`malformed`, or `oversized` state, including source list order. Every
destination participates in the target fingerprint and final currentness
check. A structurally safe target scalar, contributor/list member, series
component, description, or collection cardinality that exceeds its fixed field
bound is `oversized`; invalid typed syntax is never hidden by size. Unsafe,
private-path, credential-shaped, or otherwise invalid non-title input remains
`malformed`. For the title specifically, a private-path, credential-shaped, or
equivalently unsafe string rejects the target response with a redacted error
before a report is created. A safe oversized title or a missing, null, empty,
unloaded, or non-string title instead preserves its field-local `oversized`,
`missing`, `null`, `empty`, `unloaded`, or `malformed` state and projects no
usable snapshot title; report processing may continue. The selected file index
resolves once to an opaque identity derived by the Audiobookshelf adapter.
Final currentness locates that identity again, so a harmless file-list reorder
does not change the target.

The isolated child receives only the already-open read-only EPUB descriptor,
source fingerprint, and fixed limits. It first captures one bounded immutable
byte snapshot, verifies its digest, and uses that same snapshot for all EPUB
 parsing. It returns only bounded, source-attributed local metadata facts:
 explicit package fields, bounded opening matter, and an independent rear-spine
 copyright pass. The rear pass considers only the final eight accepted spine
 slots in reverse order, opens at most four XHTML/HTML resources, and never
 substitutes an excluded, missing, encrypted, image, unsupported, or duplicate
 slot. It admits only explicit title, creator, contributor, or byline cues and checksum-valid ISBNs when
 the same resource has a copyright marker; it is not whole-book mining.
 If that optional rear pass reaches its aggregate resource limit, it emits no
 rear facts and records a bounded warning while retaining already validated
 package and opening-matter evidence; package and opening resource limits
 remain terminal. The retained report exposes that condition as the static
 `rear_resource_limit` local reason.
 Across both pools there are at most nine content opens, 9 MiB of expanded
 content, 786432 projected characters, 150000 nodes, and 80 claims.
Each ISBN fact keeps its exact observed substring, a directly supporting
excerpt, checksum result, canonical value only when valid, and an explicit
ebook, print, unknown, or ambiguous classification with its nonnumeric label
basis. ISBN-10 remains ISBN-10; print and invalid identifiers are retained as
evidence but are never sent merely as ebook inputs. An explicitly non-ISBN
scheme or URI cannot contribute an ISBN-shaped substring.
Every report contains every supported local field in fixed order with one closed
state: `valid`, `absent`, `malformed`, `oversized`, `unsupported`, `untyped`, or
`inapplicable`. These field results are independent evidence summaries, not a
rank or decision. Each field also carries source states: Audiobookshelf target
metadata supports only title in this slice, while EPUB supports the complete
local field vocabulary. Source and DOM occurrence order is retained from the
isolated inspector through persisted replay; content-derived references never
reorder evidence. Unsafe and oversized sentinels remain non-canonical evidence.
Explicit opening-matter publisher and imprint observations remain distinct
edition claims through inspection, immutable report serialization, and exact
replay. Provider comparisons link only claims with the same role; no
publisher/imprint hierarchy is inferred.

The credential-bearing parent combines those facts with the target projection.
Every distinct checksum-valid ISBN family across bounded package, front, and
rear evidence becomes one exact discovery lane. Equivalent ISBN-10/13 values
share a lane while all original claim references, classification, position,
and provenance remain retained. When the target has one complete,
unambiguous title and author set, one work lane runs alongside the ISBN lanes;
title-only or incomplete authors suppress only that lane. More than eight ISBN
families is `query_limit_exceeded` and makes no provider request rather than
selecting a subset. Candidate admission then requires one exact normalized work title, the
complete ordered author list, one provider-local work identity, and no
translation, adaptation, or uncertain abridgement marker. Competing works and
same-title/different-author results are `no_match`.

Every referenced, enabled Hardcover connection makes at most nine fresh
credential-bearing GraphQL metadata requests. Every referenced, enabled Open
Library connection makes at most nine exact Search requests, with at most two
edition and two distinct work requests per lane, for a 45-request aggregate
maximum. All lanes share one client, Open Library pacer, response envelope,
2 MiB aggregate response-byte ceiling, and 60-second aggregate deadline.
Search admission
is bounded, deduplicated, and canonical by edition key; a third candidate is
terminal before detail requests. Open Library request starts are spaced by the
same fixed one-second pacer and are single-attempt. Disabled references produce
one `disabled` observation without client construction, credential access, or
egress. Unreferenced providers are absent.

Provider results are neutral. Zero rows are `no_match`; every valid non-empty
bounded set is `candidates`; all candidates survive in provider-identity order
with their distinct edition identity and opaque provider-local work identity.
The work lane retains only work title, complete authors, series and sequence,
description, typed taxonomy, same-work first publication, and same-work cover
evidence. It strips ISBN/ASIN, publisher/imprint, edition language,
explicit/subtitle, and every other edition claim. Hardcover preserves its
upstream category exactly: `Genre` maps to genre and `Mood` or
`ContentWarning` maps to tag; unknown, conflicting, malformed, or overflowing
taxonomy fails closed. Open Library subjects remain neutral evidence rather
than genres or tags.

Open Library additionally preserves a closed, bounded rich-evidence allowlist
from only those required edition and work records: provider identities and
revision provenance, selected identifiers and classifications, subjects,
edition characteristics, contributor identity/role, edition-work relation,
first-publication date, and cover IDs. It retains no raw payloads, arbitrary
links or unknown identifiers and performs no author traversal, edition
enumeration, fuzzy fallback, ranking, voting, or provider fusion.
Each fixed request asks for one row beyond the retained cap so overflow remains
observable. Malformed or oversized provider fields remain explicit field-local
claims; structural or join failures make the observation `malformed`. No
winner, score, merge, fuzzy search, retry, or cache exists.

Each child lane records its opaque identity, kind, matched ISBN family when
applicable, exact triggering local references, typed status, and
collision-free candidate references. The provider aggregate deduplicates the
same edition and records lanes that converge on one provider-local work.
That convergence is correlation within one source, never another vote or an
upgrade from work-scoped to edition-scoped authority.

In addition to one ABS and one declared EPUB outcome, cover-enabled providers
contribute at most eight admitted sources overall and four per provider. Exact
edition, qualified-work/default, and related-edition designations retain safe
provider/work/edition identities plus available ISBN, language, country or
publication place, publisher, publication date, and format context. Each
source records absence, timeout, rejection, unavailability, or a validated
artifact with media type, dimensions, original/preview digests, and safe
artifact references. Identical bytes share content-addressed storage while all
source provenance remains in the report. Per-provider and aggregate truncation
are explicit terminal rows. Optional Open Library cover context that exceeds
its fixed provenance bounds preserves valid metadata and records that cover as
`oversized`; it neither widens the bound nor makes the provider observation
`malformed`. Raw cover URLs and private paths are never
serialized.

The report ID is derived from the complete canonical document. Exact fresh
re-observation returns the same ID and bytes without accumulating another row.
Changed evidence creates an immutable successor and advances only the separate
current pointer. Earlier reports in that exact current schema remain retrievable
internally by stable ID.

## Authority

The command can read configured target metadata, read the selected mounted
EPUB, contact explicitly enabled referenced providers, and append neutral
reports. Its repository may initialize an absent database or open an exact
current-schema database after schema-object, foreign-key, and SQLite-integrity
checks. It validates report content and current-pointer scope only when selected
by an exact-ID/current-scope read or involved in publication; opening the
repository does not semantically audit unrelated history. It has no
backward-compatibility authority. It cannot browse inventory, review or decide
claims, apply changes, write a service or media file, run background work,
retry, prune, repair, import, downgrade, retain selectively, or migrate any
state.
