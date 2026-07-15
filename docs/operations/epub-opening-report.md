# EPUB Opening-Matter Evidence Report

`epub-opening-report` reads one authorized EPUB through a read-only logical
mount and emits one transient, source-bound report. It does not persist
evidence, retain decoded documents, contact a service, render content, execute
active content, or write to the EPUB or library.

```console
mechanical-librarian epub-opening-report \
  --config /run/config/mechanical-librarian.json \
  --mount books \
  --file relative/book.epub
```

JSON is the default. `--format text` renders the same closed fields as stable
line-oriented text. Exit `0` covers `success` and
`no_applicable_evidence`; `2` is invalid invocation; `3` is invalid
configuration/reference or unavailable source; `4` is unsupported, encrypted,
or multiple-rendition input; `5` is malformed, integrity, resource, timeout,
cancellation, or unstable-source rejection; and `70` is a child or internal
protocol failure.

## Selection and interpretation

The selector uses this versioned precedence:

1. EPUB 3 navigation landmarks for controlled opening roles;
2. EPUB 2 guide references for controlled opening roles; then
3. the earliest eligible linear spine resources in declared order.

Equal-precedence landmark conflicts are both inspected and reported as
ambiguous; they are never silently resolved. Invalid IDs and unsafe or missing
relationships are omitted with controlled warnings. Explicit `bodymatter`
semantics stop fallback. Preview, sample, and advertisement resources are
excluded only when a controlled semantic marker says so. Filenames and plain
headings never establish a body transition or exclusion.
Same-resource fragment targets remain distinct controlled boundaries: a body
or exclusion role for one fragment does not erase an opening role for another.
No fragment and empty `#` are equivalent; other fragments are decoded and
matched path-free against exactly one HTML/XHTML `id` or `xml:id`. Missing or
duplicate targets omit the role with `invalid_fragment_target`. The earliest
valid body target stops recognition, while a fragment exclusion suppresses
claims only in its target element scope. Fragment validity is resolved before
source-tier precedence: only the highest-precedence tier with at least one
surviving valid opening role may authorize claims, and the surviving canonical
role supplies the published selection path. A valid higher tier suppressed by
body or exclusion semantics still prevents lower-tier fallback. Invalid-only
optional roles suppress claims when no valid guide or spine association exists.
Raw OPF ordering and `linear` annotations are consulted only for itemrefs that
the package capability already accepted; rejected markup cannot re-enter the
opening candidate graph. Raw `linear` is valid only when absent or exactly
`yes` or `no`; other values are omitted with `unusable_spine_relationship`.

Candidate resources must be package-local and declared. XHTML is attempted as
strict XML first. Safely recoverable XHTML and declared HTML use a fresh,
bounded, deterministic `HTMLParser` projection. Recovery does not repair the
source or add base-URL, filesystem, browser, CSS, script, entity, XInclude,
XSLT, or network authority. Scripts, styles, templates, frames, embedded
objects, and structurally hidden elements do not enter the text projection.
Recovery encoding is selected only from a real XML declaration, a supported
HTML `meta charset`, or a supported `meta http-equiv="content-type"`; comments,
prose, arbitrary attributes, and script/style lookalikes are inert. Conflicting
or duplicate active attributes and unsupported declarations fail that resource
closed; equivalent separate supported declarations remain valid. XML
declarations must contain a valid version first, then optional encoding and
standalone fields in grammar order; malformed or arbitrary pseudo-attributes
fail closed. Title,
textarea, template, and foreign-namespace meta lookalikes are also inert, and
BOM-less UTF-16/UTF-32 signatures or a NUL anywhere in the already bounded
selected payload are unsupported. Foreign-namespace
local-name collisions never establish navigation, claims, body transitions,
or exclusions. Missing or empty QName prefix bindings never fall back to HTML
identity. Implicit recovered SVG and MathML subtrees remain foreign even when
their namespace declaration is omitted or the inherited default is XHTML.
Implied HTML closing-rule searches stop at foreign boundaries. Unqualified end
tag searches stay within the current contiguous namespace region and may close
the matching foreign boundary itself, but cannot reach an HTML/XHTML ancestor
beneath an open foreign boundary. Qualified ends use their binding, and
unqualified ends use the current default namespace with only explicit
empty-HTML/XHTML equivalence. Missing, empty-bound, and duplicated qualified
prefixes have opaque prefix-specific identities, so different unresolved
prefixes never alias. A self-closing end is processed only when its start was
actually pushed, and unmatched ends cannot mutate ancestor state. Namespace
declarations are grouped per prefix: unique bindings, including a unique
foreign default, remain active. A unique empty default explicitly resets to
HTML-equivalent no namespace, while a unique empty named prefix stays opaque;
only a duplicated prefix becomes ambiguous and unknown. A duplicated prefix is
malformed, not an unsupported encoding; real supported
active charset declarations still select decoding before that classification,
while foreign meta remains inert. Duplicate recovered semantic,
relationship, identity, or hiding attributes grant no order-dependent
authority. Landmark anchors with valid, invalid, or duplicate href authority
consume the navigation-link budget before target validation.
Image-only resources are classified without OCR. A defective optional
resource does not suppress claims from a safe sibling.

Claims cover title/subtitle, contributors and roles, publisher/imprint, dates,
edition/printing/revision, translation/illustration, series/order,
omnibus/anthology membership, and checksum-valid typed ISBN applicability.
Raw values and exact excerpts remain separate from normalized values. Each
citation carries path-free source and resource identities, original resource
digest, a capability-independent lineage derived from the source fingerprint
and used by F06 and F07 for same-file correlation,
semantic and selection paths, element identity,
half-open Unicode-scalar projection offsets, parser mode, and every extractor,
normalizer, parser, projection, selection, vocabulary, and schema version. The
parser identity records the actual CPython and Expat runtime revisions, and a
recovered citation carries its closed recovery-warning set.

## Closed limits

`evidence.epub_opening` selects `epub-opening-limits-v1`.

| Limit | Default | Immutable hard cap |
| --- | ---: | ---: |
| selected documents | 5 | 8 |
| expanded bytes per resource | 1,048,576 | 4,194,304 |
| expanded bytes per selection | 5,242,880 | 33,554,432 |
| text characters per selection | 524,288 | 2,097,152 |
| nodes per resource | 25,000 | 100,000 |
| nodes per selection | 100,000 | 400,000 |
| document depth | 64 | 128 |
| navigation links | 2,048 | 8,192 |
| wall time | 60 seconds | 3,600 seconds |

Child CPU is fixed at 30 seconds and post-timeout/cancellation termination
grace at two seconds. Facts remain fixed at 64, warnings at 32, ordinary exact
excerpts at 280 Unicode scalar values, and the canonical UTF-8 report at
524,288 bytes. Navigation bytes, nodes, links, and projected text count toward
the aggregate budgets, including bounded work consumed before a navigation
resource is rejected locally. An aggregate limit, timeout, cancellation,
child/output failure, or unstable source discards all partial claims.

## Privacy and qualification

The owner-invoked local response contains the bounded cited facts requested by
the command. That private response is not an ordinary log or publication
artifact. Repository evidence, CI output, issues, pull requests, and
private-corpus qualification may contain only controlled aggregate counts;
they must never contain a path, filename, title, author, identifier, source or
corpus hash, excerpt, citation, member name, raw report, or publication bytes.
No decoded navigation/content document or repaired tree is retained.

The licence-safe registry can be materialized with
`python3 scripts/materialize_epub_fixtures.py`. The exact installed console
script is then qualified with `scripts/qualify_epub_opening.py`; see
[EPUB fixture materialization](epub-fixture-materialization.md). Private corpus
paths and refresh instructions remain outside the repository. The qualifier
captures raw reports only in bounded process memory and emits one aggregate,
allowlisted JSON record. Private mode requires the exact `348` total and `347`
F06-readable expectations plus an external
`epub-opening-private-root-causes-v1` JSON record. That record maps only the
rejected relative cases to nonempty arrays of closed root-cause categories; it
remains private,
must exactly match the observed rejection set, and is never copied into the
aggregate. The qualifier fixes `maximum_document_bytes` at the EPUB package
profile's 268,435,456-byte default to reproduce the closed F06 denominator;
ordinary deployments retain the narrower 67,108,864-byte shared default. The
aggregate records only the exact accepted qualification profile and
allowlisted totals/maxima for selected documents, opened resources, expanded
bytes, projected text, nodes, navigation links, and strict/recovered documents.
Missing resources and ordinary unrecoverable malformed markup are not permitted
private-gate rejection categories. Unsafe XInclude/XSLT is separately signalled
and must match the external `unsafe_construct` root cause exactly.

The qualifier also reconstructs the canonical resource state equations. Every
resource explicitly records whether it was opened; that count must equal
`usage.opened_resources`, parser-mode counts must equal parsed resources, and
pre-open dispositions cannot claim parser work. Navigation has one exact path,
is optional but first, and may be the sole parsed resource without a content
selection. Each claim citation must identify a selected content resource with
the same parser mode and selection path, and its resource-byte digest must
equal the digest on that selected resource. These checks prevent an aggregate
from relabelling content as navigation or inventing parser work, opened
resources, or claim provenance. The canonical report binds the ordered
resource identities, byte digests, and selection paths to the same-file
lineage with `epub-opening-selection-v1`; path roles use the closed EPUB 3
landmark and EPUB 2 guide vocabularies and resource identities are unique.
