# ADR-0022: Project source-validated decision summaries without deciding

- Status: accepted
- Date: 2026-07-30
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The shadow reviewer received raw candidate values, citations, opaque
correlation IDs, date typing, and allowed bibliographic combinations. Those
facts were complete but required the reviewer to reconstruct important
deterministic distinctions: how many independent source groups supported a
candidate, whether evidence described the selected manifestation or only its
work, which creator roles were included or excluded, whether a dependent
candidate belonged to an allowed tuple, and whether date/year candidates were
consistent and writable.

Leaving this reconstruction to an untrusted reviewer weakens decisions and
makes reviewer explanations harder to audit. Replacing raw evidence with a
summary would lose information. Letting the summary choose a candidate would
move policy or decision authority across the trust boundary.

## Decision

`shadow-review-packet-v5` retains the complete packet-v4 raw projection and
adds a bounded deterministic summary to every visible proposal and candidate.
`shadow-review-request-v5` binds the resulting packet bytes. Result v1, prompt
v2, profile/policy v1, model, and reviewer authority are unchanged.

Candidate support counts are counts of distinct admitted correlation groups.
The trusted builder recomputes the one selected-EPUB identity and every
metadata-provider-authority or cover-provenance-provider identity from the
exact report and intersects those known identities with the candidate. It
never classifies an opaque hash by syntax or counts citations. Target-only
preservation is separate and cannot become independent support.

Candidate scope is exact. Work evidence is `associated_work_only`; cover
evidence is `not_applicable`; edition evidence is `established` only when the
candidate includes a selected-EPUB edition anchor or an exact accepted provider
manifestation anchor. Other edition evidence is `unresolved`. Mixed evidence
does not become established without at least one such anchor.

Complete author candidates list included author citation IDs and typed
non-author exclusions from the same represented local basis or provider
candidate/correlation. Other fields have empty role summaries. Exclusions are
retained as raw citations but never increment support.

Dependent title/subtitle/series candidates list only the existing ADR-0021
combination IDs that select their exact alternative ID. Publication candidates
state the publication logical field, exact destination, manifestation event,
represented precision, and consistency with the paired date/year alternatives.
All state `report_only=true` and `write_eligibility=unsupported`, matching the
runtime's lack of metadata-write authority.

Proposal summaries project the closed raw result and ReviewCase manual flag as
`candidate`, `keep_current`, or `manual`. A separate 0/1 field exposes a
populated current value as target-only whenever no evidence-backed alternative
preserves it, including when competing evidence produces a candidate or manual
outcome. An evidence-backed alternative that preserves the target remains
zero. These fields describe evidence sufficiency; they do not recommend, rank,
select, approve, repair, or write.

Every field has an exact closed shape. Counts reject booleans and are bounded;
role arrays are explicitly bounded to the 256-provider-correlation ceiling plus
the selected-EPUB/local correlation, multiplied by the existing
16-contribution representation ceiling; references must belong to packet
citations; combination IDs must match the exact packet tuples. The existing
packet byte limit remains authoritative and is tighter in practice. Normal
replay regenerates the ProposalSet, ReviewCase, packet, citations, and
summaries from the immutable report and requires exact canonical equality.

ADR-0013 applies. Earlier packet/request stores are rejected as non-current and
require a clean rebuild. The exact `shadow_review_requests` table stores a
`request_schema='shadow-review-request-v5'` discriminator and replay validates
it before accepting the row; there is no migration, import, or compatibility
parser. The public qualification-profile schema mechanically advances its
packet-version constant so a valid runtime profile can bind packet v5.
Private qualification assets, provider requests, scoring, prompts, result
semantics, reviewer decisions, apply, and production writes remain out of
scope.

### Pre-release packet-v6 amendment

Issue #563 extends provider-evidence citations with an exact nullable
`taxonomy_type` member so retained taxonomy values remain typed through packet
replay. `shadow-review-packet-v6` preserves the complete packet-v5 projection
and changes no decision or reviewer authority. Packet v5 is incompatible
pre-release state and is rejected rather than inferred, migrated, imported, or
converted under ADR-0013. The former request/profile lifecycle remains retired
and has no current runtime compatibility surface.

## Consequences

### Positive

- The reviewer receives explicit deterministic decision context without losing
  any raw citation or gaining authority.
- Target-only, excluded-role, wrong-scope, unresolved-manifestation, and
  unrepresentable facts cannot masquerade as independent support.
- Source reconstruction detects forged counts, bindings, roles, tuple
  membership, date pairing, or sufficiency after persistence and replay.
- Publication write limitations are explicit instead of inferred.

### Negative

- Packet/request identities change and pre-release shadow-review state requires
  a clean rebuild.
- Packets are larger, though still covered by the existing total byte bound and
  new per-summary bounds.
- Conservative manifestation binding may label plausible provider evidence
  unresolved until an exact accepted anchor exists.

### Neutral or deferred

- The summary does not alter proposal admission, candidate scoring/ranking,
  provider queries, prompt behavior, reviewer result shape, or any write gate.
- Future write support must replace the static unsupported state only through a
  separately qualified adapter/read-back contract.

## Independent title amendment

[ADR-0073](0073-independent-evidence-led-titles.md) adds a candidate-specific
ISBN-linked title exception in packet-v7 and action policy v2. Its elected title
requires no companion action; all other combination rules remain in force.
