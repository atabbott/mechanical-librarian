# ADR-0073: Audiobook tranches bind their own qualification manifest

- Status: accepted
- Date: 2026-09-10
- Issues: #967, #1047

## Context

ADR-0069 recorded that nothing automatic is ever offered a recording: the
shadow projection abstained before it read policy, so no outcome existed for a
decision to be minted from. That was true of the whole media kind, not of any
measured evidence, and it left the three decision classes F45 found dominant --
series, ASIN and narrators fill-empty -- permanently the owner's however well
they could be shown to be safe.

The ebook envelopes cannot answer for a recording. F19a and F20 measured ebook
proposals from bibliographic providers; an audiobook's evidence, its
corroboration gate and its failure modes are different. An option those
envelopes qualified must not become automatic on a recording because it shares
a category name.

## Decision

An audiobook decision identity is `category:option:audiobook_field_operation`.
The operation describes the actual effect on the report-bound current value:
preserve, fill, normalize, add or replace. Fill evidence never qualifies a
populated replacement. Valid-ASIN supersession is distinct from replacing an
invalid incumbent. The closed registry names supported field/option operations;
only identities in the published passing set grant authority.

Existing fill-only description and series options retain their meaning. Explicit
`description_fill:replace_safe_plain_text`, `series:add_qualified` and
`series:replace_qualified` choices express additional owner intent; defaults are
unchanged. The profile vocabulary is therefore an exact v3 successor.

F46a (#1047, including #1046–#1056) extends the measured operations through the
same registry. Work facts may use a complete full-title/full-author canonical
successful ASIN lookup or an independent local ASIN anchor without inheriting
recording authority. Recording identifiers retain their separate association
requirements. Complete plausible narrator-set agreement may supply narrators
without electing an ASIN or ISBN. One reliable applicable source may suffice;
provider count is not a vote.

A supported title/subtitle split is one paired effect when both policy members
are Automatic. Both current decisions and consents are required, one request
carries both values, and both readbacks must verify. An owner decision, changed
source or target, revoked consent, or either member at Ask/Ignore blocks the
pair. Partial and uncertain responses use the existing reconciliation path.
Preserving useful title/author values instead records one durable no-change
outcome and removes that work from pending application without invoking target
guards. The [automation policy](../../product/automation-policy.md) owns the
field-specific election rules and measured limits.

For audiobook synopsis, the preferred applicable safe source may be selected
while other regional descriptions remain available. Equal authority uses stable
provider/source-record identity order, not wording, length or opaque alternative
hashes. This is a deterministic choice among equally acceptable sources, not an
authority ranking. Conflicting work identity, unsafe relevant text and internally
competing texts within one record remain refusals. Ebook selection is unchanged.

Authority comes from an F46 record and nowhere else. No F19a, F20, F38 or cover
digest admits an audiobook identity, and an audiobook record admits none of
theirs. `domain/audiobook_decision_qualification.py` holds the published record
digest and the passing set; a tranche is activated by three tokens -- the
manifest file, the digest, the identity -- and no wiring change.

On a recording, `automatic` is withdrawn from every destination the passing set
does not name. Ignore, Ask and unsupported routing stay exactly as the owner
resolved them. An unqualified identity is therefore always the owner's answer
to give, never an authority failure that suspends automatic work.

Routing follows the record. An owner may route a category to Automatic once any
record qualifies it, which is how `asin` becomes routable at all; what an ebook
does under that routing is still decided by the ebook envelope, which leaves
`asin` at Ask.

## Consequences

### Positive

- A measured audiobook class can earn automatic authority through the same
  route every other option used, without widening any ebook authority.
- Adding or withdrawing qualification changes persisted interpretation. F46a
  requires a clean rebuild under report v44, proposal-set v8, shadow outcome v6,
  metadata policy profile v3 and cover automatic decision v2, with
  proposal policy `fixed-conservative-v27`, review packet v9, action policy v4
  and decision policy v14. Earlier state is
  refused without conversion or mutation, in accordance with ADR-0013.
- The report v43 provider-associated series-suffix production is replayed from
  the same candidate's complete structured association and author identity.
  Packet v9 carries recomputable recording support; it is never an independent
  source of authority.
- Packet provider linkage uses the report/proposal `canonical_asin` grammar,
  including ISBN-10-shaped identifiers. This supersedes ADR-0064's deferred
  `B0`-only linkage restriction; it does not establish recording applicability
  or grant Automatic ASIN authority by itself. Provider identity,
  claim-reference provenance and field-specific guards are still validated.
- Audio cover fill reuses the existing deterministic classifier and election,
  binds its own F46 identity, and never dispatches an image observer. A populated
  cover cannot borrow empty-fill authority. F46a's separately qualified
  replacement requires a bound incumbent shorter edge below 300 pixels and an
  applicable portrait/square alternative at least 500 pixels and twice that
  edge. Existing candidate order chooses the first eligible alternative;
  source rank alone cannot justify replacement.
- Ebook decision semantics remain unchanged; schema- and source-derived identities use exact successors.

### Negative

- Two envelopes now govern the same category names, so every authority read
  has to say which media kind it is answering for.
- The marketplace an ASIN belongs to is recorded on the provider candidate's
  evidence only. It is not surfaced on the decision or the field view, because
  carrying it there needs a `review-case-view` bump outside this unit.
- Qualification remains field/operation-specific. The first bounded record
  named six fields; F46a's measured successor names its additional operations.
  Other available candidates remain owner review.

This supersedes ADR-0069's statement that nothing automatic is ever offered a
recording. Everything else ADR-0069 records -- the case, the terminals, and the
owner's authority over an unqualified field -- is unchanged.

## Compact current records

Current F19a, F20 and F46 bindings now hash only the canonical authority object,
with shared explicit semantic/provenance source ownership. Evidence references
remain outside authority; frozen historical decoding never grants current rights.
The [qualification contract](../../product/rich-metadata-qualification.md#current-compact-authority)
owns the stable records, inventory and immutable archive. This supersedes the
cumulative full-record binding representation, preserving every operation and pair.
Outcome v9 and its exact SQL/API contract require a clean rebuild, with no adoption
or migration, because binding-derived identities and live F46 cover rules cannot
safely reinterpret predecessor stores. Empty predecessor stores also refuse.
