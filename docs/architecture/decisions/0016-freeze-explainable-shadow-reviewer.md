# ADR-0016: Freeze explainable publication-date shadow review

- Status: accepted
- Date: 2026-07-28
- Owners: project maintainers
- Supersedes: ADR-0015
- Superseded by: none

## Context

ADR-0015 froze the isolated qualification-only reviewer at
`shadow-review-packet-v1` and `shadow-review-request-v2`. Publication-date
review now needs to distinguish the source fact's exact precision and event
from the destination candidate's semantics. A provider date citation also
needs bounded, allowlisted sibling identifier and physical-format facts so the
reviewer is not asked to interpret opaque references without their context.

Those changes alter the accepted frozen tuple and trust-boundary document.
Editing ADR-0015's decision in place would erase the earlier contract, so this
decision supersedes it.

## Decision

Retain ADR-0015's isolated, qualification-only execution and persistence
boundary, but freeze the reviewer tuple as:

- model `gpt-5.6-sol` with `medium` reasoning;
- prompt `shadow-review-prompt-v2`;
- profile `codex-shadow-profile-v1`;
- packet `shadow-review-packet-v2`;
- request `shadow-review-request-v3`;
- result `shadow-review-result-v1`;
- policy `qualification-only-policy-v1`; and
- sandbox `bubblewrap-v1`.

`shadow-review-packet-v2` gives `publishedYear` candidates exactly
`manifestation_publication` plus `year`, and `publishedDate` candidates exactly
`manifestation_publication` plus `day`. No other destination may carry a date
event or precision. The cited source fact separately retains its lexical and
normalized values, exact inferred source precision, source event, linkage
confidence, provider and candidate/work identities, authority, query kind, and
immutable access time.

Every cited provider publication fact carries a `linkage` object. It contains
at most 16 sibling valid ISBN/ASIN fact objects and at most 16 sibling physical
format fact objects. Identifier facts expose only the already-admitted bounded
`reference`, `kind`, `value`, `applicability`, and `scope`; their reference list
must be a sorted, duplicate-free, exact one-to-one projection of those objects.
Format facts use the existing closed provider-evidence projection. Unknown
keys, invalid closed values, missing linkage, opaque-only identifier
references, noncanonical order, membership disagreement, or a limit breach are
rejected.

`shadow-review-request-v3` binds the exact canonical packet, qualification
profile, and generated result-schema bytes and digest. Profile tuple drift,
packet-v1, request-v2, malformed or noncanonical documents, destination/date
disagreement, provider date precision disagreement, incomplete linkage, and
over-bound context fail closed at their existing validation boundary before
reviewer invocation or persistence can grant dispatch authority. Stored
exact-current documents are validated the same way when reopened. Earlier
development state is rejected rather than migrated, imported, converted, or
repaired under ADR-0013.

The reviewer remains untrusted advice with no decision, apply, target, media,
credential, service, provider, shell, tool, network-discovery, retry, or write
authority. All descriptor binding, Bubblewrap isolation, ChatGPT-subscription
authentication, append-only attempt state, dispatch gate, result validation,
and value-free diagnostics from ADR-0015 remain in force.

## Consequences

### Positive

- Candidate date meaning is destination-exact while source precision and event
  remain independently auditable.
- The reviewer receives useful bounded identifier and format context instead of
  opaque references alone.
- The frozen tuple and its fail-closed compatibility boundary are explicit and
  reviewable.

### Negative

- Publication citations are larger, though still bounded by the existing
  1 MiB packet and 3,227,648-byte request envelopes.
- Earlier packet/request and exact-current database state cannot be replayed by
  the new runtime.
- Packet validation duplicates some report/proposal checks at the reviewer
  boundary.

### Neutral or deferred

- The additional projection contains no credentials, paths, raw provider
  payloads, service identifiers, or new write authority.
- Normal reviewer activation, alternative backends, and production bundling
  remain deferred.

## Alternatives considered

### Amend ADR-0015 in place

Rejected because it would make the accepted packet-v1/request-v2 history
incorrect and obscure the security review for a changed trust-boundary input.

### Send identifier references without fact objects

Rejected because opaque references do not give the reviewer bounded semantic
context and cannot prove exact projected membership.

### Preserve the source event on destination candidates

Rejected because a work-first source may support a target manifestation year
without changing the destination's meaning. Source and target semantics must
remain separate.

## Validation

Focused construction and canonical-replay tests cover exact provider date
precision, the complete destination/date matrix, packet candidate validation,
identifier fact/reference membership, format fact shape, missing linkage, and
both 16-fact limits. The full unit suite, architecture-boundary suite, and exact
artifact qualifier must pass at the delivered head.
