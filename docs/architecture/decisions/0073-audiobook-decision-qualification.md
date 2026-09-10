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

An audiobook decision identity is `category:option:audiobook_reason`. The first
two segments are the owner's existing policy category and option, so
`OPTION_CATALOGUE` never grows and no persisted profile is invalidated; the
third names the exact audiobook shape a record qualified. Three identities are
named: `series:primary_narrative:audiobook_fill_empty`,
`asin:preserve_exact:audiobook_gate_corroborated` and
`contributor_names:canonical:audiobook_narrators_fill_empty`.

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
- Activation is a record, not a code change, and withdrawal is the same edit
  in reverse -- but only while nothing has been persisted under the record.
  Once audiobook outcomes exist, any change to the passing set changes what
  the projection produces for cases already stored, and the
  recompute-and-compare in `adapters/persistence/metadata_reports.py` raises
  `shadow outcome persistence is invalid` for exactly those cases. This is
  true of withdrawing an identity and equally of adding a second one, which
  turns a stored `ask` into an `automatic` on replay. Nothing merges into
  that state, because no audiobook outcome can exist until the first record
  is published; unit C must define one adoption path -- the version or
  migration a passing-set change carries -- and it must cover both
  directions, before that first record.
- Ebook replay is byte-identical whatever an audiobook record says.

### Negative

- Two envelopes now govern the same category names, so every authority read
  has to say which media kind it is answering for.
- The marketplace an ASIN belongs to is recorded on the provider candidate's
  evidence only. It is not surfaced on the decision or the field view, because
  carrying it there needs a `review-case-view` bump outside this unit.
- Until a record exists the wiring is inert, which is deliberate but means the
  tranches carry no owner-visible behaviour on merge.

This supersedes ADR-0069's statement that nothing automatic is ever offered a
recording. Everything else ADR-0069 records -- the case, the terminals, and the
owner's authority over an unqualified field -- is unchanged.
