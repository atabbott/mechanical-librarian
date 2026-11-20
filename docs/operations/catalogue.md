# Current book catalogue

The catalogue is a bounded, read-only snapshot of the configured
Audiobookshelf book libraries. Review preparation refreshes it before building
[metadata reports](metadata-report.md), and every later stage checks its items
against it.

The `RefreshCurrentCatalogue` operation accepts only the current file-mode
configuration, reads no provider credential and never writes to the target or
media. Contributors can run it as the `catalogue-refresh` scenario, and read
it with `catalogue-list` and `catalogue-get`, from the
[contributor workbench](../development/contributor-workbench.md).

## Generations

- The catalogue holds at most 10,000 entries.
- A new generation is published in one SQLite transaction after the summary and
  detail `updatedAt` values are validated. The previous generation stays
  readable until then.
- A second refresh against the same database returns busy. It does not wait or
  retry.
- Changes in Audiobookshelf after the observation window are not part of that
  generation.
- A configuration change makes the catalogue report `catalogue requires
  refresh`; it never adapts a current generation silently.

The database accepts only its exact current schema
([current versions](../reference/compatibility.md#current-versions)). A
different or tampered schema fails closed; create a clean database
([clean-state rule](../reference/compatibility.md#the-clean-state-rule)).

## Entries

Each entry keeps a canonical fingerprint of the Audiobookshelf values that can
affect review evidence or currentness: item and library identity, reviewed
metadata, cover identity, file identity, media presence and media revision.
Playback, bookmark and progress fields are excluded.

When Audiobookshelf supplies a stable EPUB revision, item activity timestamps
do not invalidate completed work. When it does not, or a cover has no
independent revision, the item timestamp is kept as a conservative fallback so
changed bytes never reuse stale evidence.

An item is an audiobook when it holds audio files Audiobookshelf plays
(`audioFiles` members not marked `exclude`); `has_audio` records exactly that.
An empty or wholly excluded list is not audio.

An item holding both audio and an ebook is a recording with a supplementary
ebook, as Audiobookshelf shows it, and is prepared as the audiobook. A single
EPUB beside it is read only as corroborating evidence in the report's
`companion` section; any other ebook shape is ignored and never makes the
recording unpreparable. The ebook files' identities and revisions are part of
the fingerprint, so an edited EPUB re-prepares the item while playback does
not.

Discovery and current-item validation share one normalizer for the EPUB
location and opaque file identity, so nested, flat and path-only records bind
the same inputs. Disagreement or path traversal makes the item ineligible or
fails before any write gate. This parsing grants no media access or write
authority.

## Items that cannot be read

An item Audiobookshelf lists but cannot give usably is kept as an unread
entry: present, needing attention, with nothing to prepare. Its `unread` code
says why: `no_media` (Audiobookshelf marks it missing or invalid, for example
a book with no audio or ebook file), `unreadable` (its details are not valid
JSON or fail the media, metadata or author checks), `oversized` (its details exceed
`target_response_bytes`), or `changed` (its identity or `updatedAt` moved
between the list and the detail, or the detail answered 404). It is named from
its detail's title, else the list's title, else `Untitled item requiring
attention`. Every other item in the library is still read.

A whole library fails only for conditions of the library itself, each with a
closed code: `library_unavailable` (no such book library),
`library_too_large` (past the 10,000-item cap), `response_too_large` or
`response_unreadable` (the library list or a page). A 401, 403, other non-200
status, non-JSON answer or transport error on any request stays a
[connection failure](#connection-failures).

## Privacy

List output carries `eligible`, `ineligible`, `ebook` or `audio` facets and
never a path. An exact lookup may include the service-relative media path.
The catalogue also keeps each supported item's folder path within its
library, which only an [identifiers file](../reference/identifiers-file.md)
row is matched against, in memory.
Errors are value-free, and logs redact titles, authors, identities, paths and
credentials.

## Connection failures

When preparation meets a target TLS, authentication, authorization or
availability failure, it records a value-free condition for that connection
and pauses only that connection's queued work. Completed review packages stay
visible.

After fixing the cause, the owner selects **Check connection again**. The probe
uses the configured TLS client, requires a bounded Audiobookshelf JSON
response, and shows no endpoint, credential, path or raw transport error. A
restart returns an interrupted check to the paused state; an abandoned check is
never treated as recovery.

## Batches

A current generation can be frozen into a foreground evidence batch; see
[ebook evidence batches](ebook-evidence-batches.md).
