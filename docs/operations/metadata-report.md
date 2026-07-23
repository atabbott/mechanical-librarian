# Metadata report

The 0.4 runtime supports one synchronous report-only operation:

```text
mechanical-librarian metadata-report \
  --config /run/secrets/mechanical-librarian.json \
  --database /data/reports.sqlite \
  --connection primary \
  --library ebooks \
  --item ITEM_ID \
  --file-index 0
```

Every option is required. There are no default paths, current-directory lookup,
implicit filenames, environment fallbacks, presentation flags, or alternate
output formats.

On success stdout is exactly the persisted canonical JSON report followed by
one newline. On failure stdout is empty, stderr contains one fixed bounded
diagnostic, and the exit status is nonzero. Diagnostics never include paths,
credentials, target values, provider bodies, or EPUB text.

## Preconditions

- The database path is absent for the first invocation or already contains the
  exact 0.4 two-table schema.
- The configuration uses `config-v2` and names the requested connection and
  library explicitly.
- The target secret and each enabled referenced provider secret are owner-only
  regular files.
- Ebook libraries map to a read-only media mount.
- The selected item is an ebook and the selected file is EPUB.

Audiobook and non-EPUB targets fail before EPUB inspection, provider
construction, provider credential access, egress, or report publication.

## Evidence and replay

The command reads the current target item directly; it does not persist an
inventory. The selected file index resolves once to an opaque identity derived
by the Audiobookshelf adapter. Final currentness locates that identity again,
so a harmless file-list reorder does not change the target.

The isolated child receives only the already-open read-only EPUB descriptor,
source fingerprint, and fixed limits. It first captures one bounded immutable
byte snapshot, verifies its digest, and uses that same snapshot for all EPUB
parsing. It returns bounded title and ISBN facts.
Each ISBN fact keeps its exact observed substring, a directly supporting
excerpt, checksum result, canonical value only when valid, and an explicit
ebook, print, unknown, or ambiguous classification with its nonnumeric label
basis. ISBN-10 remains ISBN-10; print and invalid identifiers are retained as
evidence but are never sent merely as ebook inputs.
The credential-bearing parent combines those facts with the exact current
target title. Every referenced, enabled Hardcover connection makes at most one
fresh request. Disabled references produce one `disabled` observation without
secret access or egress. Unreferenced providers are absent.

Provider results are neutral. Zero rows are `no_match`; every valid non-empty
bounded set is `candidates`; all candidates survive in provider-identity order.
The fixed request asks for one row beyond the retained cap so overflow remains
observable. One malformed candidate or a cap overflow makes the entire
observation `malformed`. No winner, score, merge, fallback search, retry, or
cache exists.

The report ID is derived from the complete canonical document. Exact fresh
re-observation returns the same ID and bytes without accumulating another row.
Changed evidence creates an immutable successor and advances only the separate
current pointer. Earlier reports remain retrievable internally by stable ID.

## Authority

The command can read configured target metadata, read the selected mounted
EPUB, contact explicitly enabled referenced providers, and append neutral
reports. It cannot browse inventory, review or decide claims, apply changes,
write a service or media file, run background work, retry, prune, migrate, or
repair state.
