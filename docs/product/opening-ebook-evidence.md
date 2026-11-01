# EPUB Package and Opening-Matter Evidence

Mechanical Librarian reads each owned EPUB's package metadata, opening matter
and rear indicia, and records what they say as local evidence about the work
and edition actually in the library. Files can be repackaged, converted,
combined, mistagged or built from another edition, so every observation keeps
its raw and normalised value, source position, same-file lineage, and any
contradiction or applicability warning.

The facts are recorded in the
[metadata report](../operations/metadata-report.md). They can seed exact
catalogue queries, support a field proposal, reject an inapplicable candidate,
or establish an edition with no catalogue match
([ProposalSet](../operations/proposal-set.md),
[automation policy](automation-policy.md)). Local evidence never bypasses
policy, stale-state, capability, backup, apply, verification or audit gates.
Contradicted, malformed, ambiguous or cross-edition facts cannot enter an
automatic apply just because they came from the owned file.

For an audiobook item that also holds an EPUB, only the EPUB's title,
creator, ISBN and publisher claims are read, as corroboration. They are never
offered as the audiobook's own values, and an EPUB that cannot be read
withholds only its own evidence.

## How a file is read

An isolated child process parses one digest-verified snapshot of the
read-only file in three passes.

**Package metadata.** Observations cover every declared identifier, title,
subtitle, creator and contributor (with roles and file-as values), language,
publisher, date events, subject, description, series and position, cover
declarations, and the manifest, spine, guide, landmarks and navigation.
Package versions 1.0 and 2.x (read as OPF 2.0.1, including guide and NCX) and
3.x are supported; other versions are refused as `unsupported_version`.

**Opening matter.** Resources explicitly marked as title page, half-title,
copyright page, imprint, contributors, front matter or a similar role are
selected first, using landmarks, navigation, guide entries and
[EPUB Structural Semantics](https://www.w3.org/TR/2026/NOTE-epub-ssv-11-20260528/)
before reading order. When semantic coverage is missing, the earliest eligible
linear spine documents are read in order. Selection skips cover-only images,
already-parsed navigation, blank or script-only resources, detectable previews
and advertisements, and anything after an evidenced move into body matter. It
stops when the opening roles are covered, the body begins, or a limit is hit.
Each inspected resource records the rule and semantic term that selected it.
An encrypted or missing selected resource is recorded with a warning, not
read, and still counts toward the document limit.

**Rear indicia.** The final eight linear spine slots are considered in reverse
order and at most four XHTML or HTML resources are opened. A resource
contributes only if it carries a copyright marker (copyright, ©, "all rights
reserved", colophon or credits). Excluded, missing, encrypted, image or
duplicate slots are never replaced by another. If this pass hits its limits it
records `rear_resource_limit` and keeps what it already admitted; package and
opening limits stay terminal. Malformed or unsafe markup in a rear resource
fails the whole inspection as `malformed`, like the package and opening
passes. Position is provenance, not authority: the same admission rules apply
front and rear.

The extractor follows the
[EPUB 3.3 Recommendation](https://www.w3.org/TR/2026/REC-epub-33-20260113/).
The only vendor extension admitted is Calibre's `calibre:series` and
`calibre:series_index` pair, as a fallback that cannot outrank standard
collection metadata. Prefixed lookalikes such as `vendor:fulltitle` never gain
the authority of a standard structural-semantics term.

## What is admitted

Opening and rear matter can supply: title and subtitle; contributors with
controlled roles (author, editor, translator, illustrator, foreword,
designer, photographer, cartographer, adapter, or an explicit structured
`other`); publisher and imprint; dated events; edition, revision, printing,
translation and illustration statements; typed identifiers; series and
position; and omnibus or anthology membership. Omnibus, anthology and
navigation structure are recorded as structural facts. They supply no title
or identity authority and do not change title election or the owner's
choices.

**Titles and roles.** A chapter heading never becomes a book title because it
appears early. Author roles come only from whole attribution statements, and
a package creator with no stated role keeps no role.
Supported MARC relators map to controlled roles, and multiple roles stay on
their contributor. Book-producer (`bkp`) and other production, conversion,
scanning, software, retailer and workflow credits become typed contextual
credits, never contributors. Adjacent credits split only at another role cue
after sentence punctuation or "and": `A. B. Carter` stays whole,
`Translated by Alice. Illustrated by Bob` yields two people, and
`Translated and illustrated by Bea Artist` is one person with a compound role.

**Subtitle, publisher and imprint.** These need explicit EPUB semantics or an
exact `Subtitle:`, `Publisher:` or `Imprint:` label. Copyright holders,
printers, distributors, retailers, conversion credits, advertisements,
previews and other-work notices never satisfy them, and the same exclusion
vocabulary is re-checked after a label is stripped. Publisher and imprint stay
separate fields in source order; no hierarchy is inferred. Values are NFC with
collapsed whitespace. Repeated and conflicting occurrences are all kept with
their excerpts.

**Series.** Admitted only from a complete element whose whole text is
`Series: NAME`, `Book|Volume|Part N of|in NAME`, or, as a whole paragraph,
`NAME Book|Volume|Part N`. Positions are integer or decimal and stay lexical.
Word or Roman-numeral positions, reading lists, promotions and prose are
refused.

**Dates.** Package publication dates accept year, year-month, date, and
date-time with `Z` or a signed `HH:MM` offset from `-14:00` to `+14:00`. The
lexical value and offset are kept, not converted. An unsigned or malformed
date-time is rejected whole and never falls back to its date prefix.
Publication, work-first-publication, copyright, creation, modification and
conversion events stay distinct. Work-first-publication and creation are
work-scoped; the rest are edition-scoped, and a mismatched scope is rejected.
Generic "published" dates and print-edition dates never become ebook
publication facts. Wording that mixes a first-publication cue with this ebook
edition ("This ebook edition first published 2024") is recorded as
`ambiguous_date_event` and yields no date claim; the metadata report shows
only a missing date event. Sentence, semicolon, pipe, line and resource
boundaries stop unrelated cues from combining. The package
modification timestamp is never a publication date.

## Identifiers and editions

- ISBN checksums are validated and ISBN-10 stays ISBN-10.
- Each ISBN keeps its exact substring, a supporting excerpt, its checksum
  result and an ebook, print, unknown or ambiguous classification. Labels bind
  only to their own occurrence and never cross to a neighbour.
- A valid ISBN does not by itself identify this file's ebook edition. Print
  and invalid ISBNs are kept as evidence but never sent as ebook inputs.
- A UUID, DOI, ASIN, LCCN, URI, proprietary or package identifier never
  becomes an ISBN. A Library of Congress control number is recognised by its
  label, with or without a colon, and a bare ten-digit run inside a Library of
  Congress Cataloging-in-Publication block, with no ISBN, ebook or print label
  before or beside it, is read as one. A label alone in the element just
  before or after it counts too. The block's labelled or hyphenated ISBNs are
  still read. A heading the extractor does not read, such as one in foreign or
  promotional content, opens no block.
- The package's explicitly designated primary identifier, when it is a valid
  ISBN, applies to this EPUB. Other package ISBNs stay unlabelled unless other
  evidence establishes applicability.

Every ISBN claim gets one relationship, without the claim being changed:

- `accepted`: an explicit ebook ISBN, a valid designated primary package ISBN,
  an unlabelled ISBN family seen in both package and opening evidence with no
  conflicting occurrence, or a provider ebook ISBN in an accepted local family;
- `associated`: another ISBN on the same provider-local work as an accepted
  one;
- `unrelated`: an ISBN on a different work within that provider;
- `invalid`: failed checksum or typed validation;
- `unresolved`: valid, but its relationship cannot be established.

Relationships are per-claim: a print, ambiguous or inferred occurrence never
inherits acceptance from its family. Provider work identity is correlation
within one provider only, never independent corroboration.

Package metadata and opening matter from the same file can agree, but they are
one correlated local lineage, not two independent sources. Edition strength
comes from the combination of applicable facts, not a single score.

## Full-work identity and body completeness

An applicable opening ISBN identifies an edition claim, not a complete work:
an excerpt, preview, omnibus or file with misleading front matter can share
the opening title and ISBN. Extraction stops at the body boundary, and neither
file length nor provider agreement establishes completeness. When package or
prologue evidence materially conflicts with full-work evidence, both are kept
for owner review.

## Limits and failure

The `epub-opening-limits-v1` profile defaults to five opening documents and
fixes per-resource and per-selection byte, text, node and depth limits,
navigation links, facts, warnings and excerpts. The child has
30 seconds of CPU and a 60-second default wall clock. The opening and rear
passes share one budget of nine content opens. Each pass also has its own
byte, text, node and fact budget: the opening pass defaults to 5 MiB, 524,288
characters, 100,000 nodes and 64 facts, and the rear pass is fixed at 4 MiB,
262,144 characters, 50,000 nodes and 16 facts. Opening values and hard caps
are in `src/mechanical_librarian/domain/epub_opening.py`, and rear values are
in `src/mechanical_librarian/adapters/evidence_extractors/epub_local.py`. No
operator command changes them.

The EPUB is untrusted input. The extractor rejects archive traversal and
absolute paths; bounds entries, compression ratio, nesting, nodes and time;
disables external entities, DTD retrieval, network, scripts and remote
resources; parses only allowlisted local resource types; and never rewrites or
repairs the file. Active elements stay inert in XHTML, SVG and foreign
namespaces. Embedded instruction-like text is inert evidence.

Audiobookshelf availability never waits for extraction. A missing mount,
unstable file, malformed package, encryption, unsupported version, timeout or
absent front matter produces a typed outcome that never implies metadata is
absent and never quarantines the item.

A changed file fingerprint supersedes earlier evidence. Evidence is reused
only under the same fingerprint and the current extractor, configuration and
selection versions (recognition contract `epub-opening-v5`); citations from
another version fail closed.

## Privacy

Package metadata and opening matter are private content evidence under the
[data-privacy policy](../architecture/data-privacy.md). Reports keep only
bounded facts, fact-local excerpts of at most 280 characters, and logical
resource identities, never filesystem paths. Decoded documents, the input
snapshot and markup are never retained. Whole documents, images and raw XHTML
never reach logs, API lists, reviewer packets, exports or support bundles.

## Not supported

OCR of image-only pages, PDF extraction, full-book prose analysis, script
execution or rendered layout, fetching linked resources, DRM removal, EPUB
repair or write-back, and any deletion, conversion or replacement of media.
AZW3 and MOBI are planned ([scope](scope.md)) and need their own format,
parser and security qualification.

Contract fixtures are synthetic and licence-safe
(`tests/support/epub_packages.py`).
