# ADR-0094: Let an unconfirmed current cover take part in cover election

- Status: proposed
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1362
- Revises: ADR-0059 ("missing or inconsistent incumbent facts abstain
  automatically")
- Depends on: ADR-0093 (the deterministic stage, current-cover protection and
  the retained-cover revert)
- Supersedes: none
- Superseded by: none

## Context

ADR-0059 makes automatic cover choice abstain whenever the current
Audiobookshelf cover's identity is unknown. On the retained full-library
snapshot, 1,116 of 2,347 ebook cover considerations have a current cover of
unknown identity. That abstention, not candidate identity, is the largest
limit on automatic covers, and it leaves each of those fields with the owner
even when the current cover is fine or plainly broken.

The owner's policy is that the current cover's identity does not block a
decision; fit decides. Two facts constrain how far that can go:

- a cover chosen deliberately in Audiobookshelf is indistinguishable from a
  provider default;
- a failed read is not the same as a missing cover. A timeout or throttled read
  treated as unreadable would replace a good cover.

## Decision

A readable current cover whose identity is unconfirmed takes part in
ADR-0093's stage as a candidate instead of abstaining. It is ranked by the same
rules as the alternatives and kept unless the field is decided otherwise under
ADR-0093's current-cover protection:

- under `protected`, the default, it is replaced automatically only when it
  has a detectable issue, and any other preferred alternative is an optional
  proposal;
- under `unprotected`, it may be replaced automatically when an eligible
  candidate is preferred, including a cover the owner set by hand in
  Audiobookshelf. ADR-0093's rules prefer no different art over a fault-free
  current cover, so in practice that preference comes from ADR-0095's judge in
  automatic mode;
- under `proposals_only`, nothing is replaced automatically.

A positive contradiction on the current cover, meaning a retained identifier,
edition, work or creator disagreement, still sends the field to attention and
never to a keep. Identity grounds alone never replace a current cover.

**Unreadable** is a typed terminal outcome only: no cover, or bytes the
decoder rejects. An unreadable current cover is replaceable by the first
eligible same-work candidate. A timeout, throttling, cancellation or unknown
read result is not unreadable, stays unknown and never permits a replacement.

**The write** binds the observed target state and goes through consent,
backup readiness, plan validation, optimistic concurrency, read-back and
audit; a stale target is refused at the effect path. The replaced cover's
exact bytes are retained under ADR-0093 whether or not they decode, so a revert
restores them. A missing cover is a known-empty target with nothing to retain.
A field whose current bytes cannot be retained is not switched automatically.

Owner-final decisions and earlier owner cover decisions made in the product
are never overridden, and candidates come only from ADR-0093's eligible set.

**Shadow first.** Until the owner-run qualification held by #1418 passes (the
cover election qualification's real cohorts re-run with the new eligible set
at zero false admissions, and the replay over a full library with its cover
images), replacing an unconfirmed current cover is offered to the owner and
stays outside the qualified automatic matrix.

## Consequences

### Positive

- About half the ebook cover considerations on the full-library snapshot stop
  abstaining by default; most become keeps.
- A broken unconfirmed cover can be fixed automatically, and put back if the
  owner disagrees.

### Negative

- Keeping an unconfirmed current cover takes the field out of attention, so a
  wrong-book cover with no detectable issue and no positive contradiction is
  no longer surfaced on that ground.
- Under `unprotected`, a cover the owner set by hand in Audiobookshelf can be
  replaced. The review list and put-back are the remedy.

### Neutral or deferred

- How often the current cover matches no provider art is measured on the
  #1294 snapshot, and on #744 where perceptual hashes are persisted, during
  delivery.
- No new egress, disclosure or model input. Reasons are closed codes, and no
  cover bytes or paths reach logs, diagnostics, status or support bundles.
- Clean rebuild required under ADR-0013: the decision policy version changes.
- On acceptance, `security.md` (the cover-admission paragraph under Automatic
  decisions), `automation-policy.md` and the changelog change in the activation
  pull request.

## Alternatives considered

### Keep abstaining

It leaves the largest group of cover fields with the owner for a reason the
owner does not hold.

### Replace an unconfirmed cover whenever a confirmed candidate exists

That replaces on identity grounds alone, including covers the owner chose by
hand, and ignores the protection setting the owner set.

### Treat any failed read as unreadable

A transient failure would replace a good cover. The typed terminal outcome
keeps that from happening, as #1333 already requires of placeholders.

## Validation

- Domain tests over synthetic prospective checks, under both `protected` and
  `unprotected`: a readable unverified current cover with no detectable issue,
  kept while no judge exists; one with a detectable issue, replaced; an
  unreadable one, replaced by the first eligible same-work candidate; an
  owner-final cover, never replaced; a positive contradiction, sent to
  attention.
- A timeout, a throttled read and an unknown read result each keep the current
  cover.
- Application tests: a stale target is refused at the effect path;
  undecodable current bytes are retained, and a revert restores them.
- An offline replay over the #744 and #1294 snapshots reports, as aggregates,
  which current covers would change.
- The owner-run qualification held by #1418 before any such replacement is
  automatic.
