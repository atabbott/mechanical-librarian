# ADR-0073: Audiobook tranches bind their own qualification manifest

- Status: accepted
- Date: 2026-09-10
- Issue: #967

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
- Adding or withdrawing qualification changes persisted interpretation. This
  delivery requires a clean rebuild under report v43, shadow outcome v5,
  metadata policy profile v3 and cover automatic decision v2, with
  proposal policy `fixed-conservative-v25` under unchanged proposal-set-v7. Earlier state is
  refused without conversion or mutation, in accordance with ADR-0013.
- The report v43 provider-associated series-suffix production is replayed from
  the same candidate's complete structured association and author identity.
  Packet v8 carries recomputable recording support; it is never an independent
  source of authority.
- Audio cover fill reuses the existing deterministic classifier and election,
  binds its own F46 identity, and never dispatches an image observer. A populated
  cover cannot borrow empty-fill authority.
- Ebook decision semantics remain unchanged; schema- and source-derived identities use exact successors.

### Negative

- Two envelopes now govern the same category names, so every authority read
  has to say which media kind it is answering for.
- The marketplace an ASIN belongs to is recorded on the provider candidate's
  evidence only. It is not surfaced on the decision or the field view, because
  carrying it there needs a `review-case-view` bump outside this unit.
- Qualification remains field/operation-specific. The first bounded record
  names six fields; other available candidates remain owner review.

This supersedes ADR-0069's statement that nothing automatic is ever offered a
recording. Everything else ADR-0069 records -- the case, the terminals, and the
owner's authority over an unqualified field -- is unchanged.
