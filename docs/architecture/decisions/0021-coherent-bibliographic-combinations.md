# ADR-0021: Bind dependent bibliographic advice to coherent combinations

- Status: accepted
- Date: 2026-07-30
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Title, subtitle, and series results named dependencies but exposed only
independent alternatives. A reviewer could therefore recommend a clean title
from one representation while preserving duplicated series text in the title,
or flatten a provider's component or alternate-language work title into the
selected manifestation title. Correlation IDs explained source independence
but did not describe compatible cross-field choices.

Rewriting or splitting source strings would lose evidence and assign semantic
authority to punctuation or fuzzy title shape. Adding another model-controlled
output field would also require a prompt/result contract change outside this
product increment.

## Decision

`proposal-set-v4` adds a separate
`bibliographic-relationships-v1` projection under
`fixed-conservative-v6`. Every allowed combination explicitly covers title,
subtitle, and series. A member either references an existing alternative or
preserves the exact bound target-field fingerprint, including missing, null,
and empty states. It never edits a `ProposalResult`, alternative ID, evidence
value, or review-proposal ID. An existing alternative assessed as preserving
the target maps to the target-bound preserve member while the combination
retains its confirming source representation.

Source representations are content-addressed from a local evidence basis or
from provider authority plus candidate manifestation and work identity.
Multiple valid same-scope titles and a work title that disagrees with an
explicit edition title remain report provenance but are not admitted title
alternatives. A clean title may combine with structured subtitle/series
evidence from another representation only when that exact title alternative
has at least two independent correlation groups and source representations.

Exact normalized subtitle or series-name containment may veto a combination
that would duplicate text. It cannot create a candidate, split a title, infer
punctuation semantics, decompose an omnibus, or prefer an original or
translated title. If dependent evidence exists but no complete safe
combination survives, affected results with retained alternatives remain
`unresolved_choice` with `no_safe_bibliographic_combination`. Exact
target-bound companions keep their honest raw result and are projected as
manual without synthesizing an alternative.

`review-case-v2` projects and revalidates the relationship document against
the exact target. `shadow-review-packet-v4` includes every constrained
destination, including non-actionable preserve companions. The
`shadow-review-result-v1` shape remains unchanged. Semantic validation derives
the existing per-field candidate-or-preserve tuple and requires it to match
exactly one allowed combination; the matched combination ID is a pure audit
derivation. Per-destination proposal IDs bind the exact result set independently
of relationship tuples. Duplicate member tuples are forbidden.

ADR-0013 applies: the changed proposal-store policy accepts absent or
exact-current state only; `proposal-set-v3` is rejected. There is no migration,
import, or conversion.
Prompt, profile policy, provider queries, decisions, writes, release, and
activation remain out of scope.

## Consequences

### Positive

- Dependent advice cannot select an invented candidate or contradictory
  cross-field tuple.
- Evidence strings and per-destination audit records remain unchanged.
- Component and alternate work titles remain visible without automatically
  replacing the manifestation title.
- Proposal, case, packet, request, persistence, replay, and currentness
  identities all bind the same versioned relationship semantics.

### Negative

- Exact containment can conservatively send a plausible tuple to manual review.
- Earlier pre-release proposal databases require a clean rebuild.
- Packets may include a constrained preserve companion with no candidates.

### Neutral or deferred

- There is no generic title splitting, fuzzy matching, automatic omnibus
  decomposition, translation preference, provider ranking, human decision,
  apply, or write authority.
- Prompt and private qualification changes remain separate milestone work.
