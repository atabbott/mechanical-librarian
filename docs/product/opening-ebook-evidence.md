# Bounded EPUB Package and Opening-Matter Evidence

F07b makes the F07a opening report durable for current inventory EPUBs. It uses
an opaque selector rather than a caller path, persists fenced work before
extraction, reuses immutable evidence across restart, and supersedes current
applicability when the private source generation or governing contract changes.
This evidence remains report-only and does not yet create candidates, review
cases, decisions, or writes.

## Status and purpose

This document defines the F06/F07 EPUB evidence extraction capability.
It turns embedded package metadata and the opening matter of the owned file into
first-class, edition-aware evidence before catalogue lookup or reviewer input.

The goal is to identify the work and the edition actually present in the
library. Package metadata, title pages, copyright pages, edition statements,
and identifiers can be more applicable than a catalogue result selected only
from a title search.

Local evidence is strong but not infallible. Files may be repackaged, converted,
combined, incorrectly tagged, or built from another edition. Mechanical
Librarian therefore preserves raw observations, normalized interpretations,
same-file correlation, contradictions, and edition-applicability warnings.

## Product decision

Treat the following as first-class local evidence:

1. EPUB container and package metadata.
2. Semantically identified title, copyright, imprint, edition, contributor,
   series, and other front-matter resources.
3. A bounded fallback over the earliest eligible documents in reading order
   when semantic labels are absent or incomplete.
4. The table of contents, navigation, landmarks, and package structure where
   they clarify work composition or edition applicability.

EPUB does not have stable rendered pages. "First few pages" therefore means a
bounded set of opening resources from the package spine and landmarks, selected
by semantic role and reading order under byte, text, document-count, nesting,
and time limits.

This evidence may seed exact catalogue queries, support field proposals, reject
inapplicable candidates, or establish an edition without a catalogue match. It
never bypasses policy, stale-state, capability, backup, apply, verification, or
audit gates.

## Standards baseline

The extractor follows the package, manifest, spine, navigation, and metadata
contracts in the [13 January 2026 EPUB 3.3 Recommendation](https://www.w3.org/TR/2026/REC-epub-33-20260113/)
and the semantic terms in the
[28 May 2026 EPUB Structural Semantics Vocabulary 1.1 Note](https://www.w3.org/TR/2026/NOTE-epub-ssv-11-20260528/).
Legacy OPF/OPS 2.0.1 guide and NCX declarations remain bounded package
compatibility inputs; guide semantics participate in F07a selection.
Supported EPUB versions and tolerated legacy behavior belong in the published
compatibility matrix and contract fixtures.

Vendor extensions are observations with explicit source identity. They cannot
silently override standard metadata, but may supply useful candidates when
their meaning is documented and tested. F07a does not enable an extension
vocabulary: prefixed lookalikes such as `vendor:fulltitle` never acquire the
selection, claim, body-transition, or exclusion authority of an unprefixed
Structural Semantics Vocabulary term.

## Evidence sources

### Container and package metadata

Read the container to locate the package document, then preserve raw and
normalized observations for applicable fields such as:

- unique package identifier and every declared identifier;
- title, subtitle, short title, collection title, and display sequence;
- creator and contributor names plus declared roles and file-as values;
- language;
- publisher and imprint;
- publication, copyright, creation, and modification dates with their distinct
  semantics;
- subject, description, rights, source, relation, and type;
- collection or series membership and group position;
- accessibility and rendition facts where operationally useful;
- cover resource declarations; and
- package version, prefix mappings, manifest, spine, guide, landmarks, and
  navigation relationships.

The package modification timestamp is not a publication date. A package unique
identifier is not assumed to be an ISBN. Legacy or vendor-specific fields are
normalized only through a versioned source adapter.

Publication-typed package dates admit the existing year, year-month, date, and
UTC-`Z` forms plus a complete ISO date-time with an explicit signed `HH:MM`
offset. Signed offsets are limited to `-14:00` through `+14:00`, including the
exact boundary only at minute `00`. The extractor preserves the lexical value,
declared event, occurrence order, and applicability; it does not convert the
instant to another offset. Signed-offset creation, modification, copyright, and
unknown events remain inapplicable, as do unsigned or malformed date-times.

Declared contributor roles remain present in the raw package report. The local
projection maps supported MARC relators to controlled bibliographic roles;
multiple explicit roles remain attached to their contributor occurrence.
An exact `bkp` book-producer role and other controlled production roles become
typed contextual credits because they are not authorship or editorial
contributions. An explicit safe but otherwise unsupported structured role may
be retained as `other`; prose ambiguity never produces that fallback.

### Opening matter

Prefer resources explicitly identified as title page, half-title page,
copyright page, imprint, contributors, front matter, or another applicable
semantic role. Use landmarks and navigation before filename guesses.

F07a opening matter supports:

- displayed title and subtitle;
- author, editor, translator, illustrator, foreword, designer, photographer,
  cartographer, adapter, and structured-explicit other contributor roles;
- publisher and imprint;
- selected-manifestation publication dates plus distinct work-first-
  publication, copyright, creation, modification, and conversion events at
  their original year/month/day/timestamp precision;
- explicit edition, revision, printing, translation, or regional statements;
- ISBNs and other typed identifiers with nearby applicability labels;
- original title and original language;
- series, sequence, volume, part, or collection statements;
- omnibus, anthology, or collected-work membership;
- abridgement, illustration, commentary, or supplemental-content statements;
  and
- production notices that distinguish this file from another edition.

The same admission rules apply to already-selected front and rear copyright
pages, colophons, credits, and other indicia. Position is provenance, not
authority. Advertisements, previews, other-work notices, generic `published`
dates, and print-edition dates do not become selected-ebook manifestation
facts. Conversion, scanning, software, retailer, and workflow credits remain
typed contextual evidence and never enter contributor candidates.
Target-work subtitle, publisher, and imprint are admitted in either position
only from explicit EPUB semantics or an exact `Subtitle:`, `Publisher:`, or
`Imprint:` label. Publisher and imprint remain different edition fields.
Copyright holders, printers, distributors, retailers, conversion credits, and
promotional or other-work labels do not satisfy those cues. The same complete
contextual-credit and promotion vocabulary is applied again after stripping an
exact field label, including conversion, scanning, production, distribution,
retail, and workflow forms. Nested elements that project the same labelled
source occurrence emit it once, while genuinely repeated sibling occurrences
remain distinct. Canonical subtitle, publisher, and imprint values use NFC and
collapsed whitespace in both front and rear positions. Every admitted
occurrence retains its bounded supporting excerpt and source position;
repetition and conflicts remain evidence rather than being resolved, and rear
position supplies no authority boost.

Date-time evidence requires either `Z` or an explicit signed offset; an
unsigned date-time is rejected as a whole and cannot fall back to its valid
year, month, or day prefix. Wording that mixes a work-first cue with the
selected ebook edition, such as `First published as this ebook edition 2024`
or `This ebook edition first published 2024`, is unresolved regardless of cue
order or case: the opening report records `ambiguous_date_event`, while neither
front nor rear projection emits a typed date claim. Sentence, semicolon, pipe,
newline, and resource boundaries prevent unrelated cues from being combined.
The retained metadata report currently has no separate unresolved-date field,
so this condition is represented there by the absence of either date event
rather than by guessing.
Work-first-publication and creation events are work-scoped; manifestation,
copyright, modification, conversion, and unknown events are edition-scoped,
and durable local-claim construction and replay reject a mismatched scope.

Adjacent explicit contributor credits are split only at another controlled
role cue after sentence punctuation or `and`. This keeps initials such as
`A. B. Carter` intact, recovers separate people from `Translated by Alice.
Illustrated by Bob`, and retains `Translated and illustrated by Bea Artist` as
one compound role on one contributor.

Only the minimum supporting text is retained. Story prose adjacent to front
matter is not automatically evidence of title, author, edition, or series.
When both publisher and imprint are explicit, the local projection preserves
their source order and separate edition roles. It does not infer a hierarchy,
promote one role into the other, or compare unlike roles.
The existing explicit `Book|Volume N[.N] of|in Series` pattern remains a local
series-and-position cue. An unqualified trailing
`Series Name Book|Volume N[.N]` form is not admitted because it cannot be
distinguished proportionately from directives, promotions, and story prose.
Word or Roman-numeral positions are not admitted by the trailing form.

### Navigation and structure

Navigation and the table of contents may identify an omnibus, anthology,
multi-volume work, included novella, or supplemental material. Structural facts
remain distinct from bibliographic claims. A chapter heading does not become a
book title merely because it appears near the start.

## Deterministic selection

### Semantic pass

Select applicable resources referenced by package metadata, landmarks,
navigation, guide entries, or structural semantics. Record the selection rule
and semantic terms that caused each resource to be inspected.

### Reading-order fallback

When semantic coverage is missing, inspect the earliest eligible linear spine
resources in order. `epub-opening-limits-v1` defaults to five qualifying
opening documents and closes the document, resource/selection byte, text,
node, depth, navigation-link, time, fact, warning, excerpt, and report bounds.
The immutable values are published in the
internal EPUB inspection boundary; it has no standalone operator command.

Skip or separately classify:

- cover-only image resources;
- navigation documents already parsed structurally;
- blank, decorative, or script-only resources;
- samples, previews, advertisements, and unrelated excerpts when detectable;
- documents after an evidenced transition into body matter; and
- encrypted, unsupported, or unreadable resources.

Namespace-qualified start, end, and self-closing tags use one resolved-name
model during recovery. Active elements remain inert in XHTML, SVG, and foreign
namespaces, while their closing tags cannot strand active state and suppress a
later visible sibling.

Selection stops when the required opening roles have been inspected, the body
transition is reached, or any hard resource budget is exhausted.

## Extraction and normalization

Package parsing, XHTML text extraction, claim recognition, normalization, and
policy are separate stages.

Each observation records:

- source file fingerprint and format version;
- package and resource identity;
- manifest, spine, landmark, guide, navigation, or fallback selection path;
- semantic role;
- resource-relative element path and bounded text offsets;
- exact supporting text or raw metadata value;
- normalized field, value, type, and contributor role;
- extractor, normalization, schema, vocabulary, projection, selection, and
  actual parser-runtime versions;
- language and encoding facts;
- a capability-independent lineage derived from the source fingerprint and
  shared by F06 and F07 for same-file correlation; and
- ambiguity, malformed-input, edition-applicability, and contradiction
  warnings.

The extractor preserves raw values before normalization. It does not rewrite
the EPUB, repair package metadata, fetch remote resources, execute scripts,
apply stylesheets, or infer hidden browser layout.

## Identifier and edition rules

Identifiers are typed and validated independently.

- Validate ISBN checksums and preserve ISBN-10 versus ISBN-13.
- Preserve labels that distinguish ebook, print, hardcover, paperback, audio,
  regional, or other applicability.
- Do not assume every valid ISBN identifies the file's ebook edition.
- Preserve multiple valid identifiers when the front matter legitimately lists
  several formats.
- Treat an unlabelled identifier near format lists as ambiguous until another
  fact establishes applicability.
- Never convert a UUID, DOI, ASIN, LCCN, proprietary ID, URI, or package
  identifier into an ISBN-shaped field. Applicability labels are bound to their
  own ISBN occurrence and cannot cross a neighbouring identifier.

The package document's explicitly designated primary identifier is distinct
from other declared identifiers. When that explicit primary identifier is
itself a checksum-valid ISBN, it is applicable to the EPUB publication. A
parser-inferred package identifier and every secondary package ISBN remain
unlabelled unless other evidence independently establishes applicability; the
parser warning and source provenance remain available. Package and opening
observations retain their separate provenance even when their ISBN-10/ISBN-13
family agrees.

The report derives one closed relationship for every ISBN claim without
discarding or rewriting the claim:

- `accepted` means an explicit ebook ISBN, a checksum-valid explicitly
  designated primary package ISBN, or an unlabelled ISBN family observed in
  both package and opening evidence when every occurrence in that family is
  likewise unlabelled and non-conflicting;
- `associated` means an alternate ISBN on the same provider-local work as an
  accepted identifier;
- `unrelated` means an ISBN on a different work from an accepted identifier
  within that provider;
- `invalid` means checksum or typed-state validation failed; and
- `unresolved` preserves a valid claim whose relationship cannot be established
  from the bounded evidence.

Provider work identity is correlation within one provider only. It is never
independent corroboration, a winning candidate, or proposal authority.
Associated, unrelated, invalid, and unresolved claims remain available as
typed evidence but are not promoted into accepted evidence.
Relationships are claim-local: print, ambiguous, conflicting, and inferred
package occurrences never inherit acceptance merely because another occurrence
in the same ISBN family is accepted. Provider-local work identity may prove
association for such an occurrence; without that proof it remains unresolved.

Qualification keeps two denominators explicit. Accepted-evidence precision
scores only distinct ISBN families whose relationship is `accepted`.
Accepted-identifier recall asks whether each accepted gold ISBN is present
among all retained checksum-valid observations, so relationship typing cannot
hide recall loss by discarding associated or unresolved evidence. Associated,
unrelated, invalid, and unresolved counts are published separately.

Edition strength is based on the combination and applicability of facts, not a
single universal score. An explicit edition statement plus matching title,
contributors, publisher, date, and applicable identifier is stronger than a
bare package title. Package metadata and front matter from the same file can
corroborate internally, but remain one correlated local lineage rather than two
independent sources.

## Candidate and policy use

Local file evidence is available before remote candidate retrieval. It may:

- construct a typed identifier lookup;
- narrow title and contributor searches;
- distinguish work-level from edition-level candidates;
- reject a candidate for another format, region, translation, omnibus, or
  revision;
- support a proposal without a remote catalogue result; or
- create a contradiction that remains visible for policy or human review.

Package and front-matter facts are not merely prompt hints. They are immutable
evidence records referenced directly by candidates, proposals, decisions, and
apply provenance.

Strong applicable local evidence may qualify a non-destructive automatic field
decision after shadow evaluation. Contradicted, malformed, ambiguous, or
cross-edition facts cannot enter automatic apply merely because they came from
the owned file.

## Security boundaries

EPUB input is an untrusted archive containing untrusted XML, XHTML, images,
fonts, stylesheets, scripts, links, and metadata.

The extractor must:

- reject archive traversal and absolute paths;
- bound archive entries, compressed bytes, expanded bytes, compression ratio,
  nesting, XML nodes, attributes, text, and execution time;
- disable external entities, DTD retrieval, network access, script execution,
  and remote resource loading;
- parse only allowlisted local package and content resource types;
- avoid materializing the complete archive when streaming or bounded reads are
  sufficient;
- isolate parser failures from the durable job worker;
- clean temporary artifacts on success, failure, cancellation, timeout, lease
  recovery, and process restart; and
- classify encrypted, DRM-protected, malformed, oversized, and unsupported
  content without blocking ingestion or other evidence sources.

Embedded prompt-like text is inert evidence. It cannot alter extraction rules,
request secrets, invoke tools, or become reviewer instruction.

## Privacy and retention

Package metadata and opening matter are private library and content evidence
under the [data-privacy policy](../architecture/data-privacy.md).

- F07a returns normalized facts and minimum cited spans only in the
  owner-invoked transient response; it persists none of them.
- Keep complete decoded documents transient. F07a and F07b do not retain a
  decoded document, repaired tree, or diagnostic markup artifact. Any future
  diagnostic retention requires separately tracked policy and acceptance.
- Never place whole opening documents, images, or unrestricted XHTML in logs,
  ordinary API lists, reviewer packets, exports, or support bundles.
- Stage only cited spans and explicit facts for a reviewer.
- Use logical resource identifiers rather than personal filesystem paths.
- Permit the owner to inspect and delete retained content artifacts without
  changing the EPUB or target library item.

## Availability and failure behavior

Audiobookshelf availability does not wait for extraction. A missing mount,
unstable file, malformed package, encrypted resource, unsupported version,
parser timeout, or absent front matter produces typed evidence and operational
outcomes. It does not imply that metadata is absent and does not quarantine the
item.

A changed file fingerprint supersedes prior extraction work. Cached evidence is
reused only when the source fingerprint, extractor version, configuration, and
selection contract remain applicable.
The current material-recognition contract is `epub-opening-v3`. Opening
citations produced by `epub-opening-v2` are incompatible and fail closed rather
than entering cache reuse or report replay. This compatibility change does not
alter the persisted inspection or metadata-report schema versions.

## Qualification plan

### Synthetic contract fixtures

Cover at minimum:

- EPUB 2 and EPUB 3 package and navigation variants;
- standard and vendor-specific metadata;
- title, half-title, copyright, imprint, contributor, and edition pages;
- missing or incorrect package metadata corrected by front matter;
- package and front-matter contradictions;
- ebook and print ISBN lists with clear and ambiguous labels;
- translations, illustrated editions, revised editions, regional editions,
  omnibuses, anthologies, and collected works;
- multiple contributors and role vocabularies, including compound roles and
  production-role exclusion from the bibliographic projection;
- contributor-role and manifestation-publication-date recall plus explicit-
  negative precision over deterministic synthetic year/month/day/timestamp,
  signed-offset, print/original-work, conflict, repetition, and bound cases;
- paired front/rear target-work subtitle, publisher, and imprint semantics,
  exact labels, source bounds, conflicts, repetition, and copyright-holder,
  printer, distributor, retailer, conversion, advertisement, preview, and
  other-work negatives;
- series names and integer, decimal, labelled, missing, and strictly bounded
  trailing sequence values;
- image-only title or copyright pages without OCR fallback;
- misleading early chapter headings and unrelated previews;
- malformed XML/XHTML, invalid encodings, duplicate IDs, and broken links;
- archive traversal, entity expansion, compression bombs, oversized documents,
  deep nesting, hidden text, and hostile embedded instructions; and
- encrypted, unsupported, truncated, and concurrently changing files.

Fixtures are generated or licence-safe and contain no real library facts.

### Shadow qualification

Measure:

- extraction coverage by field and semantic source;
- identifier validity and edition applicability;
- accepted-identifier precision and recall, with associated, unrelated,
  invalid, and unresolved ISBN counts reported separately;
- package versus front-matter agreement and contradiction;
- correct work, edition, contributor role, series, and omnibus classification;
- target-work subtitle/publisher/imprint recall and explicit-negative precision
  by front/rear source position, reported only as aggregate-safe counts;
- candidate retrieval improvement;
- false replacement, human correction, and unresolved rates;
- parser failures and bounded-resource enforcement;
- runtime, memory, temporary storage, and artifact retention; and
- behavior across supported EPUB versions and producer toolchains.

Field-specific policy eligibility is versioned. A parser, vocabulary,
normalizer, selection, or material configuration change creates a new
evaluation cohort.

## Explicit exclusions

The initial extractor does not perform:

- OCR of image-only front matter;
- broad PDF extraction;
- full-book prose analysis;
- JavaScript execution or rendered-browser interpretation;
- network retrieval of linked resources;
- DRM removal or decryption;
- EPUB repair or metadata write-back; or
- automatic deletion, conversion, or replacement of media files.

AZW3 and MOBI support reuse the evidence contract only after separate format,
parser, security, and compatibility qualification.

## Decisions required before implementation

- Select the archive, XML, and XHTML parser dependencies and record their
  licences and security posture.
- Finalize soft and hard document, byte, text, node, nesting, and time limits.
- Define the stable resource and text-span provenance scheme.
- Define supported EPUB 2, EPUB 3, legacy guide, navigation, and vendor metadata
  behavior.
- Select allowlisted vendor extensions and their precedence.
- Define body-transition and unrelated-preview classification rules.
- Define identifier applicability and field-specific automatic-policy
  thresholds.
- Decide whether bounded diagnostic XHTML artifacts are ever retained.

These decisions are recorded in configuration contracts, compatibility data,
and an ADR where they create a durable architectural commitment.
