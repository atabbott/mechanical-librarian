# ADR-0008: Extend transient EPUB evidence with bounded opening parsing

- Status: accepted
- Date: 2026-07-15
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR-0007 isolates F06 package extraction and deliberately keeps navigation and
content opaque. F07a needs evidence from selected opening resources, including
hostile XHTML and recoverable HTML, without changing the package capability or
granting a parser network, filesystem, rendering, persistence, credential, or
write authority. Selection must remain subordinate to F06 validation, and a
child report must not survive if its source provenance is stripped or forged.

## Decision

1. The transient child completes the unchanged F06 package capability first,
   then passes its typed report to a separate opening adapter. Concrete
   adapters do not import one another. Only package-declared local members may
   be reopened through the validated descriptor and `ZipFile.open`; no member
   is extracted.
2. EPUB 3 landmarks, EPUB 2 guide references, and accepted linear spine
   declarations form one bounded candidate graph in that order. Raw OPF is
   consulted only to recover ordering, fragment, and `linear` annotations for
   exact declarations F06 already accepted. Rejected, duplicate, child-bearing,
   wrong-parent, or wrong-namespace itemrefs never re-enter the graph. Raw
   `linear` has authority only when absent or exactly `yes` or `no`; every other
   value is omitted with the existing unusable-spine warning.
3. Same-resource candidates are opened once. Recognition-relevant opening
   roles are retained with their source tier, role, path order, and hashed
   decoded fragment. Fragment validity is resolved before source-tier
   precedence. Only the highest-precedence tier with at least one surviving
   valid opening role may authorize recognition. After boundary validation,
   one surviving role supplies the closed canonical selection path. A valid
   higher tier suppressed by body or exclusion semantics still prevents
   lower-tier fallback, and a lower tier can never authorize a claim published
   under a higher-tier path. When every optional opening role is invalid and no
   valid guide or spine association exists, recognition remains suppressed.
   No fragment and empty `#` are equivalent. Nonempty fragments resolve only
   to one unique HTML/XHTML `id` or `xml:id`; missing or duplicate targets omit
   that role with a controlled warning. The earliest valid body target stops
   recognition, while exclusion targets suppress only their element scope, so
   safe earlier or distinct opening material remains available. Raw IDs and
   fragment text never enter the public report.
4. Each selected XHTML resource receives a fresh Expat parser first. Safely
   recoverable XHTML and declared HTML receive a fresh bounded standard-library
   `HTMLParser`. Recovery has no renderer, URL loader, CSS, script, base URL,
   XInclude, XSLT, filesystem, or network integration and never rewrites the
   source.
5. Recovery recognizes encodings only from a BOM, a real XML declaration, a
   supported HTML `meta charset`, or a supported content-type `http-equiv`
   declaration. The codec set is closed. Unsupported or conflicting active
   declarations reject the resource. Duplicate encoding, charset, or
   content-type attributes fail closed; equivalent supported declarations in
   separate active locations remain compatible. Comments, prose, arbitrary
   attributes, script/style, title/textarea, template content, and foreign
   namespace meta lookalikes are inert. XML encoding authority requires the
   exact declaration grammar: valid `version` first, optional `encoding`, then
   optional valid `standalone`; arbitrary, reordered, duplicate, adjacent,
   unquoted, or unterminated pseudo-attributes fail closed. Any NUL anywhere in
   the already bounded selected payload and BOM-less UTF-16/UTF-32 signatures
   are unsupported.
6. HTML/XHTML element capability governs navigation ancestry, anchors, title
   heuristics, claim elements, body transitions, and exclusions in strict and
   recovered modes. Foreign local-name collisions are inert for those
   semantics. Missing or empty QName prefix bindings receive an explicit
   unknown foreign identity in start/end matching rather than falling back to
   HTML. Implicit recovered SVG and MathML subtrees retain foreign identity
   when their namespace declaration is omitted, including beneath an inherited
   XHTML default. Implied HTML closing-rule searches stop at a foreign boundary.
   Explicit end-tag searches are confined to the current contiguous namespace
   region. A matching foreign boundary may close itself, but an end tag cannot
   reach an HTML/XHTML ancestor beneath an open foreign boundary. Qualified ends
   retain their bound identity. Unqualified ends resolve through the current
   in-scope default namespace, with only the parser's explicit empty-HTML/XHTML
   equivalence; they are not namespace wildcards. Missing, empty-bound, and
   duplicated qualified prefixes receive opaque prefix-specific identities, so
   the same unresolved QName can close its own start without aliasing a
   different unresolved prefix. Self-closing recovery syntax invokes end
   handling only when its corresponding start was pushed. Unmatched ends cannot
   mutate inherited namespace state. Namespace declarations are grouped per
   prefix: unique nonempty bindings, including a unique foreign default, remain
   authoritative. A unique empty default resets recovery to the HTML-equivalent
   no-namespace model, while a unique empty named prefix remains opaque. Only a
   duplicated prefix becomes ambiguous and unknown. Any duplicated prefix is
   malformed rather than an encoding failure, but that
   malformedness is tracked independently so a real supported active charset
   declaration can decode the payload before malformed classification. Foreign
   meta remains inert under a unique foreign default even when another prefix
   is duplicated. Duplicate
   recovered semantic, relationship, identity, and hiding attributes grant no
   authority. Every eligible landmark anchor with a present or duplicate href
   consumes the navigation-link budget before target validation.
   Conservative active-element suppression still applies across namespaces,
   while otherwise renderable foreign text remains bounded data.
7. A versioned deterministic projection excludes active and structurally
   hidden elements. Exact excerpts use half-open Unicode-scalar offsets in that
   projection. No decoded document, navigation tree, repaired tree, or
   diagnostic markup artifact is retained.
8. Any succeeded report that carries resources or nonzero parser usage binds
   its source fingerprint, fingerprint-derived same-file lineage, and ordered
   selection digest as one set. Parsed resource records carry the exact
   resource-byte digest used by their citations, and that digest also
   participates in the selection digest. Canonical hydration and the parent
   protocol reject missing or inconsistent resource, citation, and lineage
   provenance. A pristine pre-open no-applicable report and every failed
   envelope remain empty of parser state.
9. The opening capability retains the transient child controls from ADR-0007,
   uses its closed `epub-opening-limits-v1` profile, emits one canonical
   versioned opening-report object (currently `epub-opening-report-v2`), and
   treats a parsed navigation document as a
   valid sole resource without requiring content selection.

## Consequences

### Positive

- Opening evidence remains subordinate to the already validated package graph.
- Hostile markup receives bounded, least-authority parsing with deterministic
  provenance and typed failure behavior.
- Same-resource semantic interactions cannot silently erase title evidence or
  cause duplicate opens.

### Negative

- Selection retains a small amount of internal fragment and role state that is
  intentionally absent from the public report.
- Strict and recovery parsers both consume bounded logical work when strict
  XHTML fails before safe recovery.

### Neutral or deferred

- The report is transient and introduces no persistence schema or migration.
- No evidence is submitted to policy, reviewers, providers, or target writes.
- Browser rendering, OCR, CSS interpretation, script execution, and arbitrary
  character-set detection remain outside this capability.

## Alternatives considered

### Expand ADR-0007 in place

That would rewrite an accepted package-only decision and obscure when hostile
content parsing entered the trust boundary.

### Parse every manifest or spine resource

This would ignore semantic selection, increase attacker-controlled work, and
weaken the observable opening-only outcome.

### Use a browser or general repair parser

Those systems add rendering, URL, recovery, and dependency behavior that is
unnecessary for bounded textual evidence and harder to close deterministically.

## Validation

- Unit and property cohorts cover EPUB 3/EPUB 2 role order, fragment boundary
  interactions, strict/recovered parsing, real and inert encoding declarations,
  invalid spine partitions, provenance stripping, foreign namespace
  collisions, active content, and safe sibling continuation.
- The public deterministic fixture registry exercises strict and recovered
  resources plus same-resource EPUB 3 and EPUB 2 boundary interactions.
- Integration and installed-wheel qualification exercise canonical child
  hydration, least-authority composition, limits, privacy, and process cleanup.
- Linux qualification retains the non-root, read-only, no-network controls and
  immutable process limits established for transient EPUB evidence.
