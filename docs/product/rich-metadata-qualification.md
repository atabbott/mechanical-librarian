# Qualification records

A qualification record is the only source of Automatic authority. It names the
exact policy options and operations that may be decided without the owner. It
grants no write permission, builds no target write and changes no provider
selection; every write still passes the guarded-effect checks in
[ADR-0037](../architecture/decisions/0037-origin-neutral-guarded-field-effects.md).
How each field uses that authority is in the
[automation policy](automation-policy.md).

## Current compact authority

Three current records each bind one `authority` object:

| Record | Media | Authority holds |
| --- | --- | --- |
| [Ebook core fields (F19a)](../reference/f19a-qualification-current.json) | Ebook | Permitted operations for title, contributors, date, language and publisher |
| [Ebook rich metadata (F20)](../reference/f20-rich-metadata-qualification-current.json) | Ebook | Permitted rich-metadata options and a disposition for every rich option |
| [Audiobook (F46)](../reference/f46-audiobook-qualification-current.json) | Audiobook | Named audiobook operations, each as `category:option:audiobook_<field>_<operation>` (for example `genres:add:audiobook_genres_fill`), plus the title/subtitle pair; any other audiobook operation is owner-only |

The object holds the family, media and permitted operations, plus the ebook
rich-metadata dispositions and the audiobook paired operations. Its canonical
JSON SHA-256 is the runtime constant for that family, and every automatic
decision carries it. The compiled runtime reads no JSON record, Git history or
network archive; the record exists so the constant can be checked.

Only the current digest decodes. A decision minted under any other digest is
invalid, and upgrading across an authority change needs a clean rebuild
([upgrades](../operations/upgrades.md)).

### Ebook rich-metadata dispositions

| Disposition | Meaning | Options |
| --- | --- | --- |
| `passing_automatic` | May be Automatic | `subtitle:fill_empty`; all three ebook `series` fill options; `genres` fill, add and replace; `tags` fill, add and replace; `description_fill:safe_plain_text`; both `isbn_form` options |
| `not_qualified` | Owner only | `subtitle:corroborated_split`, `description_fill:google_visible_text` |
| `insufficient_coverage` | Owner only | both `asin` options |
| `policy_only` | Not a write; stays the default | `tags:off` |
| `passing_human_only` | Owner only | `explicit:unsupported` |

Owners keep Ask me or Ignore for every option that is not Automatic.

### Separate records

These are pinned by their own constants in `src/` and are not part of the three
records above:

- The [contributor-display record (F38)](../reference/f38-contributor-display-qualification-manifest-v1.json)
  grants only `contributor_names:canonical:provider_consensus_display`.
- The cover election qualification grants ebook
  `cover_order:fixed_source_order`.

No record crosses media or family: the two ebook records, the
contributor-display record and the cover digest admit no audiobook identity,
and the audiobook record admits no ebook option
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).

## When authority changes

Under [ADR-0087](../architecture/decisions/0087-operation-bound-qualification-authority.md)
authority is the permission set, not the source code.

- **Rotates:** adding, removing or changing a permitted operation, disposition
  or pair, or changing which target write a permitted operation may perform.
- **Needs new measurement:** widening (a new operation, or a broader write
  under an existing one). Add fresh aggregate evidence to the provenance record.
  Narrowing rotates the digest without new measurement.
- **Does not rotate:** every other source change, including evidence, preview,
  copy, persistence, scheduling and provider code. Such a change needs the same
  behaviour, security and build proof as any other change.

When authority rotates, update the record and its runtime constant together,
then run:

```sh
uv run python scripts/verify_qualification_records.py
```

It checks that each record's shape is closed and that each runtime constant,
found as its top-level literal assignment, equals the digest of its record's
authority.

## Provenance

The shared [provenance record](../reference/qualification-provenance-current.json)
holds the measurements behind the current grants: the audiobook measurement,
the shared ebook measurement, the ISBN measurement, the measurement artifacts
and their immutable origins. Existing measurements are frozen and never
regenerated; a widening adds new evidence beside them. It claims no fresh
holdout, no rescored historical quality and no production write. Its stated
limitations apply: the retained development sources show that current consumers
match the measured results, not library-wide accuracy or coverage.
