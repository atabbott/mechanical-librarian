# ADR-0093: Choose covers by measured rules, protect fault-free current covers, and keep replaced covers for revert

- Status: accepted
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1363
- Revises: ADR-0059 (the replacement test for a confirmed current cover)
- Supersedes: ADR-0073's audiobook cover replacement test, and the matching
  audiobook paragraph of ADR-0059
- Superseded by: none

## Context

ADR-0059 replaces a confirmed current cover automatically only when a
candidate's complete seven-part rank is strictly better. That rank is mostly
pixel area, so it says nothing about whether the owner would want the change,
and on the retained full-library snapshot it keeps 1,217 confirmed current
covers whatever their fit. Audiobooks follow a separate small-incumbent test
(ADR-0073, #1055).

An owner labelled 201 cover considerations (771 covers) on 2026-10-24. Three
measured facts came out of it:

- **Shape.** No chosen cover had the wrong shape for its medium, against 17%
  of rejected covers.
- **Duplicates.** Lower-resolution copies of art that was available sharper
  are 4% of chosen covers and 34% of rejected ones.
- **Resolution.** The median short edge is 526 px for chosen covers and
  333 px for rejected ones.

Between different arts, size predicted little: in labelling on 2026-10-25,
the owner's six chosen edition covers measured 304 to 415 px, and a 999×1500
alternative was at best a maybe.

On a pre-registered one-shot holdout of 57 decisive cases, the rule "exclude
the wrong shape, collapse the same art, keep the current cover, then
resolution" matched the owner in 77% of cases, against 72% for always keeping
the current cover. No reviewer observation dimension qualified as a veto
(#1417). In an offline replay over 1,603 cover fields, the stage takes 65% of
them out of attention at current admission.

A 2026-10-30 synthetic-mix library showed the cost of having no default: 16 of
17 cover attention items came from one branch that asks the owner whenever a
verified current cover has any other retrieved image. Separately, 18 of its 24
works had an alternative with the same perceptual hash as the current cover.

Switching covers automatically over a populated target needs a way back.
Today the product keeps prior cover bytes only for recovery and removes them
on every verified terminal path, and owner-entered values have no revert
([ADR-0085](0085-owner-entered-values-withdrawal-and-reselection.md)). After an
automatic switch the replaced cover could exist nowhere the product may offer
it again, which is most likely for a cover only the library held.

## Decision

### One deterministic stage

Cover election runs one pure domain stage, per medium, over the
identity-eligible candidate set of one consideration. It reads no reviewer
observation and makes no model or provider call.

1. **Shape.** An ebook never takes square art. An audiobook takes portrait art
   only when no square art at or above the 300 px floor exists, for the
   current cover as for candidates. Shape and size are read from the
   image's oriented dimensions, as it displays after any rotation the file
   records.
2. **Same-art collapse.** Candidates whose perceptual, colour, layout and
   aspect measures all fall within the near-duplicate study's thresholds form
   one group, represented by its sharpest member. The thresholds are named,
   documented constants. Hashes come from a 320×480 preview of the decoded
   pixels in the constrained decoder path, never from stated sizes, so the
   thresholds keep their measured meaning. Collapse runs only inside one
   consideration's eligible set, removes copies and never admits a candidate
   or establishes identity, because publisher templates collide across works.
3. **Resolution.** Decides only between copies of the same art, with a
   materiality margin so near-equal copies do not churn.
4. **Size floor.** Between different arts the only size rule is a floor of
   300 px on the short edge.
5. **Omnibus.** One volume's cover is never eligible for an omnibus.

The remaining eligible set keeps ADR-0059's order with its portrait-sane step
replaced by step 1: verified manifestation before verified work, then pixel
area, width, height, source precedence and the opaque reference.

### Keep unless a detectable issue exists

After the stage, the current cover is kept unless it has a detectable issue
from this closed list, recorded on the decision as a closed reason:

- the wrong shape for its medium;
- a sharper copy of the same art exists;
- below the 300 px floor;
- missing or unreadable;
- an edition language label outside the library's configured languages.

The same checks exclude candidates. A current cover with a detectable issue is
decided as a known-empty target is today: the first candidate in the order
above, except that a sharper copy of the same art takes that copy, and a
cover below the floor is cured by a copy of the same art at or above it. A library
title that names a different work from the one most eligible candidates show
is not a switch: it holds cover selection and sends the title disagreement to
review first.

No size difference between different arts ever replaces a readable current
cover that has no detectable issue.

ADR-0094 replaced the first delivery's rule that an unreadable current cover is
never switched automatically: one that is missing, or whose exact bytes are
bound, may now be.

The cover packet keeps one image per preview, so a larger original whose
preview is the current cover's own is bound to the current cover rather than
reviewed. The stage still sees it: the largest such original, when its short
edge is longer than the current cover's and no reviewed copy of the same art is
at least as large, is offered as one more candidate with the current cover's
confirmed identity. It is kept only when the stage takes it for an undersized
cover or as a sharper copy; otherwise the stage is decided without it. The
consideration then lists it as a `larger_copy` candidate. The packet and its
one-candidate-per-preview rule are unchanged.

### Current-cover protection

One owner setting, `review.cover_current_protection`, closed to exactly three
values. Configuration admission refuses any other.

| Value | Automation may replace a current cover |
|---|---|
| `protected` (default) | only when it has a detectable issue; any other preferred alternative is an optional proposal |
| `unprotected` | also when an eligible candidate is preferred, including a cover the owner may have set by hand in Audiobookshelf |
| `proposals_only` | never; fields with a detectable issue become proposals |

In every mode, owner-final decisions and earlier owner cover decisions made in
the product are never overridden, candidates come only from this stage's
eligible set, and every automatic switch goes through the guarded effect path
and appears in the review list. The store-managed value changes only through
the session-, CSRF- and Origin-bound settings command with an optimistic
revision; a file-managed value is read-only in the browser. A change makes
pending automatic cover decisions stale, and none executes under the old
value. When the work is prepared again, an automatic choice that was never
written is withdrawn and the new value decides again. This stage prefers no different art over a fault-free current cover,
so until a later preference source exists (ADR-0095), `unprotected` decides as
`protected` does, unless the edition option below is on.

**Edition match.** `review.cover_prefer_edition`, a boolean defaulting to
`false`, is the edition-match option for owners who want it. When it is on, a
verified same-edition candidate of a different art from a work-level current
cover is a preferred alternative: a proposal under `protected` and
`proposals_only`, and automatic only under `unprotected`. It never adds a
detectable issue.

### Retained replaced covers and revert

When an automatic decision replaces a current cover, the original the guarded
pipeline read before the write is kept as an owner-private, digest-addressed
artifact bound to that decision. Its digest must equal the plan's pre-value
digest. It is kept until the owner keeps the decision or the entry leaves the
bounded review list, then removed through a persisted retryable obligation, as
recovery bytes are today. It is never exported, logged, offered as a download,
placed in a support bundle or sent to a reviewer, and its private path appears
nowhere. A field whose replaced cover cannot be retained is not switched
automatically: while the store is full, the switch is suggested instead, and
a write that races to a full store still stops.

Retention runs inside the guarded write path, so it also covers the automatic
switches made before this decision is accepted, such as ADR-0073's audiobook
switches: when the store is full, or the copy does not hash to the pre-value,
the write stops as stale with the reason `cover_retention` and nothing is sent.
No automatic switch happens without a copy to put back.

The bounded review list is the most recent 1,000 verified automatic switches
(`MAXIMUM_LISTED_SWITCHES`). When a switch verifies and pushes the oldest one
off the list, that original's removal is queued in the same transaction, so
continuous switching settles near 1,000 kept originals, well inside the
2,000-entry and 2 GiB bound, which is reached only if removals keep failing.

A revert, shown as Put back, is an ordinary owner cover decision that chooses
the retained original, offered in the next comparison as a retained
alternative, through the owner-confirmed retained-cover path (#1078),
with its `BEGIN IMMEDIATE` selection token and every effect guard. It is
refused as stale if the field changed since. This is a cover revert for
automatic decisions only; owner-entered values keep ADR-0085's rule that
rollback and undo are unavailable. ADR-0094 and ADR-0095 reuse this mechanism
and define none of their own.

### Attention and the review section

Only cover fields that need the owner count in attention: a missing or
unreadable cover with nothing to choose, a suspected wrong book, or an open
choice with no default.
Optional improvements, such as proposals or alternatives beside a kept cover,
have their own count, which is display only and grants nothing.

The cover review section (#1633) lists proposals, open choices and automatic
decisions, newest first beside the cover each replaced. There are no bulk
actions: each confirm, pick, keep or put-back binds its exact consideration,
proposal and target selection token and is one owner decision through the
existing guarded command. Pages are bounded, private, `no-store` and carry
previews only. The list filters by detectable-issue reason, so one rule's
switches can be reviewed together.

### Shadow first

Until the owner-run qualification held by #1418 passes (the replay over a full
library with its cover images, and the cover election qualification re-run
with this stage's eligible set and zero false admissions), a rule-made switch
reaches the owner as a proposal and stays outside the qualified automatic
matrix. It is admitted only under its own shadow identity,
`cover_order:fixed_source_order:measured_replacement`, which no shipped
qualification manifest names, so it binds only a future record for exactly
that identity. Automatic cover decisions keep every existing guard: consent, backup
readiness, capability, plan validation, optimistic currentness, read-back and
audit.

## Consequences

### Positive

- A readable current cover with no fault stays without asking, which takes
  most cover fields out of attention without a reviewer.
- Every automatic switch can be put back, including a cover only the library
  held.
- One protection setting governs all automatic cover replacement, so ADR-0094
  and ADR-0095 widen nothing on their own.

### Negative

- Retaining replaced originals is new retention of library content, bounded
  by the review list and up to 8 MiB each, the existing cover byte bound.
- More candidate sources mean more distinct arts per field. With #1380's wider
  candidate pool the stage takes only 47% of fields out of attention, because
  more fields hold several distinct arts.
- `unprotected` can replace a cover the owner set by hand in Audiobookshelf,
  which the product cannot tell apart from a provider default.

### Neutral or deferred

- The ADR-0059 seven-part strictly-better replacement test and the ADR-0073
  audiobook small-incumbent test no longer apply to replacements, and the
  `replace_if_strictly_better` cover option is removed. Fill of a known-empty
  target keeps the first-ranked rule. A small audiobook cover that ADR-0073
  replaced automatically is now suggested until the measured replacement
  qualifies; the shipped F46 record is unchanged and still names the retired
  operation, which nothing admits.
- Decision reasons, protection modes and counts are closed values. No cover
  bytes, retained-artifact path, URL or title reaches a log line, diagnostic,
  status or support bundle. The stage adds no disclosure to any provider or
  reviewer.
- Clean rebuild required under ADR-0013: the proposal and decision policy
  versions, `config-v11` and the retained-cover bindings change persisted
  state. The bindings are append-only with immutable-row
  triggers.
- `security.md`, `data-privacy.md`, `configuration.md`,
  `automation-policy.md`, `review-experience.md` and the changelog changed in
  the activation pull request (#1363).

## Alternatives considered

### Keep the seven-part strictly-better rank

It replaces covers by pixel area and keeps confirmed covers whatever their
fit. The measured rule matched the owner more often.

### Use reviewer observations as vetoes

No observation dimension qualified on the labelled set (#1417).

### Proposals only as the default

Nothing would switch automatically, and fields with a detectable issue would
all wait for the owner. It stays available as `proposals_only`.

### Reject some proposals, then confirm the rest in one gesture

The design-stage review allowed a page-bounded batch. The owner chose no bulk
actions (2026-11-07), matching the review contract's rule of no bulk
decisions.

## Validation

- Domain tests over synthetic fixtures for each rule edge: shape per medium,
  including an audiobook with no square art; a same-art group from several
  sources, and two works whose publisher template collides, which must not
  collapse; the materiality margin on both sides; the floor at 299 and 300 px;
  each detectable issue, including the title that names a different work; an
  omnibus offered one volume's cover; each protection mode with a current
  cover that has a detectable issue and one that has none; the edition option
  in each mode.
- Synthetic owner-final, held and active-fetching considerations are never
  switched.
- Application tests: a retained original whose digest differs from the plan's
  pre-value is refused; a switch whose retention fails is not admitted; a
  revert through the #1078 path succeeds and one after the field changed is
  refused; a protection change makes pending automatic decisions stale; a
  stale item in the review section is refused without failing others.
- A redaction test feeds cover bytes, a retained-artifact path, a URL and a
  title in and asserts none comes out.
- An offline, read-only replay over the #744 and #1294 snapshots reports per
  rule, as aggregates, which current covers would change. A rule whose input a
  snapshot does not persist is reported as not replayable, never inferred.
- The owner-run qualification held by #1418 before any rule-made switch is
  automatic.
