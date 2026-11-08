# Qualification records

An option can be Automatic only when a qualification record names it. The
record lists the exact options and operations that Mechanical Librarian can
decide without you. It grants no write permission and prepares no write to your
library. It also leaves the choice of sources unchanged. Mechanical Librarian
still checks every write as
[ADR-0037](../architecture/decisions/0037-origin-neutral-guarded-field-effects.md)
requires. How each field uses Automatic is in the
[automation policy](automation-policy.md).

## Current compact authority

Each of the three current records holds one `authority` object:

| Record | Media | What it holds |
| --- | --- | --- |
| [Ebook core fields (F19a)](../reference/f19a-qualification-current.json) | Ebook | Permitted operations for title, contributors, date, language and publisher |
| [Ebook rich metadata (F20)](../reference/f20-rich-metadata-qualification-current.json) | Ebook | The rich-metadata options it permits, and a qualification result for every rich-metadata option |
| [Audiobook (F46)](../reference/f46-audiobook-qualification-current.json) | Audiobook | Named audiobook operations, each written as `category:option:audiobook_<field>_<operation>` (for example `genres:add:audiobook_genres_fill`), and the title/subtitle pair. You decide every other audiobook operation |

Each `authority` object holds the family, the media and the permitted
operations. The F20 object also holds the rich-metadata results, and the F46
object holds the paired operations. The code holds each object's SHA-256 digest,
taken over its canonical JSON. Every automatic decision carries the digest that
covers its option. The installed product reads no JSON record, Git history or
network archive. Each record exists so that its digest can be checked.

Mechanical Librarian rejects a stored decision that carries any digest other
than the current one. Upgrading across a change to a record's digest needs a
clean rebuild ([upgrades](../operations/upgrades.md)).

### Ebook rich-metadata results

| Result | Meaning | Options |
| --- | --- | --- |
| `passing_automatic` | Can be Automatic | `subtitle:fill_empty`, all three ebook `series` fill options, `genres` and `tags` fill, add and replace, `description_fill:safe_plain_text` and both `isbn_form` options |
| `not_qualified` | Never Automatic | `subtitle:corroborated_split`, `description_fill:google_visible_text` |
| `insufficient_coverage` | Never Automatic | both `asin` options |
| `policy_only` | Writes nothing. It is the default | `tags:off` |
| `passing_human_only` | Never Automatic | `explicit:unsupported` |

You keep Ask me or Ignore for every option except `explicit:unsupported`,
which is always Not supported.

### Separate records

These have their own digests in `src/` and are not part of the three records
above:

- The [contributor-display record (F38)](../reference/f38-contributor-display-qualification-manifest-v1.json)
  grants only `contributor_names:canonical:provider_consensus_display`.
- The cover election qualification grants ebook
  `cover_order:fixed_source_order`.

Each record covers one family and one kind of media. The two ebook records, the
contributor-display record and the cover election qualification grant no
audiobook operation. The audiobook record grants no ebook option
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).

## When a record changes

Under [ADR-0087](../architecture/decisions/0087-operation-bound-qualification-authority.md),
a record's digest covers what it permits, not the code.

- The digest changes when a change adds, removes or alters a permitted
  operation, result or pair. It also changes when a permitted operation can
  make a different write to your library.
- Widening needs new measurement too. Widening means a new operation, or a
  broader write under an existing one. Add fresh aggregate evidence to the
  provenance record. Narrowing changes the digest without new measurement.
- No other code change alters the digest. That includes changes to evidence,
  preview, wording, storage, scheduling and source lookups. Such a change needs
  the same proof of behaviour, security and build as any other change.

When the digest must change, update the record and its digest in the code
together, then run:

```sh
uv run python scripts/verify_qualification_records.py
```

It checks that each record has exactly the fields it should. It also checks
that each digest in the code equals the digest of its record's `authority`
object. It reads that digest from the one top-level assignment that sets it.

## Evidence for the grants

The shared [provenance record](../reference/qualification-provenance-current.json)
holds the measurements behind the current grants. They are the audiobook
measurement, the shared ebook measurement and the ISBN measurement. The record
also holds the measurement files and their origins, which never change. Existing
measurements are frozen and never run again. A widening adds new evidence beside
them. The record does not claim a test on fresh held-out cases, and it does not
rescore past quality. No measurement wrote to a live library. The limits the
record states also apply. For example, the record's development data shows that
the parts of the product that use these grants match the measurements. It does
not show accuracy or coverage across a whole library.
