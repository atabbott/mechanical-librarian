# ADR-0039: Continuous guarded application without target CAS

Status: Accepted

[ADR-0085](0085-owner-entered-values-withdrawal-and-reselection.md) amends manual holds:
admission carries a work's active hold to its successor case.
Issue #1370 amends consent across an owner's policy activation: see
[Consent across policy activation](#consent-across-policy-activation).

## Context

ADR-0037 and the Audiobookshelf canary proved the common guarded field-effect
kernel, but Audiobookshelf does not provide a supported value-and-version
compare-and-set operation. Continuous operation cannot depend on that service
capability arriving later. The product must retain useful automatic and human
application while making the remaining same-field race explicit and bounded.

## Decision

Compose the guarded Audiobookshelf adapter into the installed review runtime
only after an authenticated owner grants library-scoped consent for the currently
eligible subset of the existing 14 ebook destinations. Connection unavailability
blocks consent, while an insufficient permission or incompatible field excludes
only that destination; a library needs at least one eligible destination. Any new
destination, action kind, or authority scope requires fresh consent. Consent also binds the exact resolved
policy semantics and qualified automatic categories, so a semantic policy
change invalidates it before scheduling and again at claim. Consent is not
inferred from a decision, configuration, or an earlier canary, and a policy
activation only carries consent the owner already gave. Activity discloses every included and excluded destination before the
owner submits the exact subset. The command accepts only an identical fresh
probe result, and partially consented libraries remain available for later
incremental consent.

Use the normalized value of the exact destination as the target version. A
change to another Audiobookshelf item field therefore does not invalidate an
otherwise current plan. A change to the planned destination does. Immediately
before a claim, recheck durable decision authority, policy identity, consent,
dependencies, backup readiness, capability state, pause state, and that exact
field value. After the claim, issue exactly one destination-minimal request and
read the destination back through the existing ADR-0037 reconciliation path.
Audiobookshelf still has no CAS: another actor can change the same field between
our final read and the service accepting our request. The product states that
limitation and never blindly retries an ambiguous request.

Run human and qualified automatic decisions through the same field-effect
kernel. One instance-scoped mutation slot serializes every library and origin;
durable selection alternates away from the last library when another is ready.
Siblings remain independent. A manual decision supersedes only unclaimed
automatic authority for that field and records an explicit active manual hold.
Returning to automatic first requests a fresh exact-current scan and retains the
hold until that named scan completes successfully. The release compares the
durable hold token, so a later manual decision cannot be released by an older
scan; incomplete, failed, or interrupted scans leave automatic scheduling off.
A superseded unclaimed plan may
be planned again only after current authority, target, dependency, consent, and
capability checks; its complete event history remains intact. A policy
demotion or generation change prevents unclaimed automatic work from becoming
current, while already claimed work finishes to its durable checkpoint.

Pause and library consent revocation stop new claims. They do not interrupt a
claimed service request. Startup recovers only claimed, applying, verifying, or
reconciling work and its persisted actions and cleanup obligations. Backup
readiness means the exact-current SQLite store passes its consistency check and
is outside a transaction so the documented stopped-volume backup can be made;
it is not a claim that an external backup copy exists.

Add Activity as the only new permanent navigation destination. Its ordinary
history read makes no backup-readiness claim and does not select a permission
example. Opening the write-permission disclosure explicitly checks readiness
and presents bounded per-destination scope, representative before/after context,
safety constraints, and confirmation. The fresh consistency check and permission
snapshot must name the same database revision; drift is unavailable and requires
an explicit retry. A separate generation-owned query-only reader keeps this
check independent of history and root owner admission. Both readers drain before
their dependencies close. The response is a point-in-time disclosure, not perpetual
atomicity: confirmation and preclaim retain their fresh checks and authority.
Library, Attention,
Policy, and Detail retain their accepted hierarchy and language. Capability
failure removes only the affected field from actionable Library and Attention
projections while Detail retains its visible read-only evidence and explanation;
it does not create a queue console or dashboard. Denied observations are
persisted. A successful read-only observation clears only an older permission,
field-shape, or connection-availability suspension it can disprove; it never
clears `unexpected_effect_behavior` and preserves a later failure even when
serialized application work resumes after an observation.

### Consent across policy activation

Amended by #1370, because activating an edited policy silently withdrew every
library's write permission and the product then showed writes as calm.

1. An owner's policy activation carries each library's consent that is current
   for the outgoing resolved policy to the incoming one, inside the
   activation's own transaction and before its state changes. Connection,
   library, destination and action are identical, and the consent records the
   activation's transition and the consent it replaced as its basis. A replay,
   a no-op or a refused activation carries nothing. Entering Preview from
   automatic at the unbound first generation resolves every configured
   library, as activation does, and later generations reuse the stored
   resolutions, so a consented library holding no current work keeps its
   consent.
2. Consent is still never inferred from a decision, configuration, a canary or
   any other policy path, and a carry never widens it.
3. A changed destination set or envelope schema, or a library the active policy
   does not resolve, still lapses consent. Activity and Library state the
   lapse with its reason, and Activity never reads as calm while no library
   holds consent.
4. Plans are not rewritten. A plan made under the replaced consent goes stale
   at its claim, and current work is planned under the carried consent.

## Consequences

The installed product can continuously apply current human and qualified
automatic decisions to explicitly consented disposable or operator-selected
Audiobookshelf libraries. Normal media mounts remain read-only; embedded-file
writes, rollback/Undo, destructive operations, bulk approval, and production
qualification remain out of scope. Cover upload continues to use the immutable
artifact and private recovery seams.

The remaining target race is intentionally visible and covered by qualification.
Useful field-local progress is an owned product capability and has no upstream
CAS prerequisite, but the product does not claim cross-system atomicity. All 14 destinations, both
origins, multiple libraries, restart, drift, pause, revocation, manual hold, and
capability suspension must qualify through the exact installed artifact and
supported service versions. Deterministic fake-service qualification owns the
exhaustive ambiguity, bounded-retry, policy-demotion, and scheduling-fairness
proof that cannot be induced safely or repeatably through the service.

The database remains pre-release exact-current under ADR-0013. No migration,
release, deployment, tag, package publication, or production-service authority
is created by this decision.
