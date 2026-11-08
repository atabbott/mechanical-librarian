# EPUB evidence

Mechanical Librarian reads the package metadata, the opening pages and the
copyright or credits pages at the back of each EPUB in your library. It
records what they say as evidence about the work and edition of your copy. A
file can be repackaged, converted, combined, mistagged or built from another
edition. So each fact keeps its raw and normalised value, where it was found,
which file it came from, and any warning about a contradiction or a different
edition.

The facts are recorded in the
[metadata report](../operations/metadata-report.md). They can start an exact
catalogue search or support a proposed value. They can also rule out a match
that does not apply, or establish an edition no catalogue lists
([ProposalSet](../operations/proposal-set.md),
[automation policy](automation-policy.md)). Evidence from the file still
passes every check: policy, unchanged library values, supported writes,
backups, write permission, read-back and the audit record. A contradicted,
malformed, ambiguous or other-edition fact is never written automatically,
even though it is in your file.

When an audiobook item also holds an EPUB, only the EPUB's title, creator,
ISBN and publisher are read. They help find and confirm the book, and are
never offered as the audiobook's own values. An EPUB that cannot be read adds
no evidence, and the audiobook is still prepared.

## How a file is read

An isolated child process reads one digest-checked snapshot of the read-only
file in three passes.

The first pass reads the package metadata: every declared identifier, title,
subtitle, creator and contributor, with their roles and file-as values. It
also reads language, publisher, date events, subject, description, series and
position, cover declarations, and the manifest, spine, guide, landmarks and
navigation. Package versions 1.0 and 2.x are read as OPF 2.0.1, including the
guide and NCX, and 3.x is supported. Any other version is refused as
`unsupported_version`.

The second pass reads the opening pages. It first picks documents marked as
title page, half-title, copyright page, imprint, contributors, front matter or
a similar role. The marks come from landmarks, navigation, guide entries and
[EPUB Structural Semantics](https://www.w3.org/TR/2026/NOTE-epub-ssv-11-20260528/),
and they outrank reading order. Where the marks fall short, it reads the
earliest eligible documents in reading order. It skips cover-only images,
navigation already read, blank or script-only documents, and previews and
advertisements it can detect. It also skips anything after the point the file
marks as the start of the body. It stops when the opening roles are covered,
the body begins or a limit is reached. Each document read records the rule and
semantic term that picked it. A picked document that is encrypted or missing
is not read. It is recorded with a warning and still counts toward the
document limit.

The third pass reads the back of the book. It takes the last eight documents
in reading order, from the end, and opens at most four XHTML or HTML
documents. A document counts only if it has a copyright marker: copyright, ©,
"all rights reserved", colophon or credits. A skipped document (excluded,
missing, encrypted, an image or a repeat) is not replaced by an earlier one, so
the pass never looks past the last eight. If this pass reaches a size or fact
limit, it records `rear_resource_limit` and keeps what it found. In the package
pass, a size limit ends the inspection. So does a size limit on all the opening
pages together, but one opening document over its own limit is skipped with a
warning. Malformed or unsafe markup at the back fails the whole inspection as
`malformed`, as in the package pass. In the opening pages, such a document is
skipped with a warning. Front and back use the same rules to recognise facts,
and a fact's position adds no weight.

The extractor follows the
[EPUB 3.3 Recommendation](https://www.w3.org/TR/2026/REC-epub-33-20260113/).
The only vendor extension it reads is Calibre's `calibre:series` and
`calibre:series_index` pair. It is a fallback and never outranks the standard
collection metadata. A prefixed lookalike such as `vendor:fulltitle` is never
treated as a standard structural-semantics term.

## What is recorded

The opening and back pages can supply the title and subtitle, the publisher and
imprint, dated events, identifiers with their type, and series and position.
They can supply contributors, each with a role from a fixed list: author,
editor, translator, illustrator, foreword, designer, photographer, cartographer
or adapter. A role the metadata states that is not on the list is recorded as
`other`. They can also supply edition, revision, printing, translation and
illustration statements, and membership of an omnibus or anthology. Omnibus,
anthology and navigation structure are recorded as facts about the file's
structure. They are never used as the title or to say which work the file is.
They never change the chosen title or your choices.

Coming early does not make a chapter heading the book's title.
The author role comes only from a whole attribution line, such as "By" or
"Author:" and the names. A package creator with no stated role keeps no role.
Supported MARC relators map to the fixed roles, and a contributor keeps all
their roles. Book-producer (`bkp`) and other production, conversion, scanning,
software, retailer and workflow credits are kept as credits of their kind,
never as contributors. Adjacent credits split only where a full stop,
semicolon, pipe, line break or "and" comes before another role cue.
`A. B. Carter` stays whole, `Translated by Alice. Illustrated by Bob` gives two
people, and `Translated and illustrated by Bea Artist` is one person with both
roles.

A subtitle, publisher or imprint needs an explicit EPUB semantic term or an
exact `Subtitle:`, `Publisher:` or `Imprint:` label. Copyright holders,
printers, distributors, retailers, conversion credits, advertisements,
previews and notices of other works never count. The same exclusions are
checked again after a label is removed. Publisher and imprint stay separate,
in the file's order, and neither is assumed to belong to the other. Values are
in Unicode NFC with runs of spaces collapsed. Repeated and conflicting values
are all kept with their excerpts.

A series is read only from a whole element whose text is `Series: NAME` or
`Book|Volume|Part N of|in NAME`, or from a whole paragraph that reads
`NAME Book|Volume|Part N`. A position is a whole number or a decimal, kept as
written. Positions in words or Roman numerals are refused, and so are reading
lists, promotions and prose.

A package publication date can be a year, a year and month, a full date or a
date-time. A date-time needs `Z` or a signed `HH:MM` offset from `-14:00` to
`+14:00`. The value and offset are kept as written, not converted. A date-time
with no offset, or a malformed one, is rejected whole and never falls back to
its date part.

Publication, first publication of the work, copyright, creation, modification
and conversion stay separate events. First publication and creation belong to
the work, the rest to the edition, and an event on the wrong one is rejected.
A generic "published" date or a print edition's date never becomes the
ebook's publication date. Wording that ties a first-publication cue to this
ebook ("This ebook edition first published 2024") is recorded as
`ambiguous_date_event`. It gives no date, so the metadata report shows only a
missing date event. Cues across a sentence end, semicolon, pipe, line break or
document boundary are never combined. The package modification timestamp is
never a publication date.

## Identifiers and editions

- ISBN checksums are validated and ISBN-10 stays ISBN-10.
- Each ISBN keeps its exact text, an excerpt around it, its checksum result
  and whether it is for the ebook, print, unknown or ambiguous. A label applies
  only to the ISBN it labels, never to its neighbour.
- A valid ISBN does not by itself identify this file's ebook edition. Print
  and invalid ISBNs are kept as evidence but never used as the ebook's ISBN.
- A UUID, DOI, ASIN, LCCN, URI, proprietary or package identifier never
  becomes an ISBN. A Library of Congress control number is recognised by its
  label, with or without a colon. Inside a Library of Congress
  Cataloging-in-Publication block, a bare run of ten digits is read as one
  too, unless an ISBN, ebook or print label sits before or beside it. A short
  line directly before or after it that is only such a label, such as "eBook
  ISBN", "ISBN-10:" or "(paperback)", counts as beside it. The block's
  labelled or hyphenated ISBNs are still read as ISBNs. A heading the
  extractor skips, such as one inside SVG or other non-HTML markup or in
  promotional content, starts no block.
- When the package names a valid ISBN as its primary identifier, that ISBN
  applies to this EPUB. Other ISBNs in the package are not taken to apply
  unless other evidence shows they do.

Each ISBN gets exactly one of these relationships, and the ISBN itself is not
changed:

- `accepted`: an explicit ebook ISBN, or a valid ISBN the package names as
  primary. An ISBN with no label is accepted when the package and the opening
  pages both give it, in either form, and nothing conflicts. So is a source's
  ebook ISBN that matches an ISBN accepted from the file.
- `associated`: another ISBN that one source lists for the same work as an
  accepted ISBN.
- `unrelated`: an ISBN that the same source lists for a different work.
- `invalid`: its checksum or its format check failed.
- `unresolved`: valid, but its relationship cannot be established.

Each place an ISBN appears gets its own relationship. A print, ambiguous or
inferred one is accepted only on its own evidence, never because the same ISBN
was accepted elsewhere in the file or in another form. A source that
puts two ISBNs on one work links them within that source only, never as
independent confirmation.

The package metadata and opening pages can agree, but they come from one file
and count as one source, never two. How strongly an edition is established
depends on which applicable facts agree. No single score decides it.

## Partial files

An applicable ISBN in the opening pages names an edition, not a complete work.
An excerpt, preview, omnibus or file with misleading front matter can share the
opening title and ISBN. The opening pass stops where the body begins, and
neither file length nor agreement among sources shows the book is complete. When
package or opening evidence conflicts on something material with evidence about
the whole work, both are kept for you to review.

## Limits and failure

The `epub-opening-limits-v1` profile reads at most five opening documents by
default. It also limits bytes, text, nodes and depth for each document and
for the whole selection, plus navigation links, facts, warnings and excerpts.
The child process has 30 seconds of CPU time and a default wall clock of 60
seconds. Between them, the opening and back passes open at most nine
documents. Each pass also has its own budget. The opening pass defaults to
5 MiB, 524,288 characters, 100,000 nodes and 64 facts. The back pass is fixed
at 4 MiB, 262,144 characters, 50,000 nodes and 16 facts. Opening values and
hard caps are in `src/mechanical_librarian/domain/epub_opening.py`, and
back-pass values are in
`src/mechanical_librarian/adapters/evidence_extractors/epub_local.py`. No
setting or command changes them.

Every EPUB is treated as untrusted. The extractor refuses absolute paths and
paths that leave the archive. It limits entries, compression ratio, nesting,
nodes and time. It turns off external entities, DTD retrieval, network access,
scripts and remote resources. It parses only allowlisted local resource types
and never rewrites or repairs the file. Active elements stay inert in XHTML,
SVG and other namespaces, and text that reads like an instruction is only
evidence.

Audiobookshelf stays available while the extractor runs. A missing mount, an
unstable file, a malformed package, encryption, an unsupported version, a
timeout or absent front matter gives a named outcome. That outcome never means
metadata is missing and never quarantines the item.

When a file's fingerprint changes, its earlier evidence is replaced. Evidence is
reused only with the same fingerprint and the current extractor, configuration
and selection versions (extractor `epub-opening-v5`). A citation from another
version is refused.

## Privacy

Package metadata and opening pages are private content under the
[data-privacy policy](../architecture/data-privacy.md). Reports keep only the
facts, an excerpt of at most 280 characters for each, and a hashed reference
to each document inside the EPUB, never a filesystem path. Decoded documents,
the input snapshot and markup are never kept. Whole documents, images and raw
XHTML never reach logs, API lists, reviewer packets, exports or support
bundles.

## Not supported

Mechanical Librarian does not use OCR on image-only pages, extract PDFs,
analyse whole-book prose, run scripts, render layout or fetch linked
resources. It does not remove DRM, repair or write back to an EPUB, or delete,
convert or replace media. AZW3 and MOBI are planned ([scope](scope.md)), and
each needs its own format, parser and security review.

The test EPUBs are synthetic and safe to distribute
(`tests/support/epub_packages.py`).
