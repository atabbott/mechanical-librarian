# ADR-0085: Owner-entered values, withdrawal and guarded revert

- Status: accepted
- Date: 2026-10-24
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

Amends [ADR-0030](0030-authenticated-review-web-boundary.md) (reversal),
[ADR-0037](0037-origin-neutral-guarded-field-effects.md) (rollback) and
[ADR-0039](0039-continuous-guarded-application.md) (manual holds).

## Context

An owner could only accept one retained alternative or keep the current value.
A title, subtitle or publisher with no eligible alternative offered nothing to
choose, genres were all or nothing, a recorded decision could only be replaced,
and a written decision could not be undone except by a corrective
`keep_current` on the same case, which writes back the case target rather than
what the write replaced (#1285). Every guarded write already records the value
it read back immediately before writing (`pre_value`), in its plan and in every
append-only event, so the pre-image is durable.

## Decision

- **Vocabulary.** Human decisions move to `human-decision-v3`, with outcomes
  `accept`, `keep_current`, `reject` (historical), `owner_entered`, `withdraw`
  and `revert`, and one new required key, `reverts_plan_id`, which is `null`
  except on a revert and is part of the request digest. `decision_actions` is
  unchanged, so the attention entry predicate, consent and policy are too.
- **Owner-entered values.** Title, subtitle and publisher take owner text
  through `validation.owner_text_value`: 1 to 512 code points, already NFC with
  whitespace collapsed, and no control, path, credential or URI text. The server
  refuses a value that fails (`owner_value_invalid`) or equals the current one
  (`owner_value_unchanged`); it never rewrites what was sent. Suggestions
  (`title_case`, `strip_part`, `split_author`, `combined`) are pure, at most
  four, read only the current value, and only fill the input. Genres take any
  non-empty subset, of at most 32, of the union of current and eligible
  proposed genres, in union order. Bulk editing and every other destination
  stay excluded. No proposal, shadow, automatic-decision, policy or
  qualification path reads an owner-entered or revert value.
- **Withdraw.** `withdraw` appends a row with no value and revokes the
  decision's unclaimed authority in the same transaction. A field is decided
  while its latest row is anything but `withdraw`, so a withdrawn actionable
  field returns to Attention. Any human row still blocks automatic handling, so
  a withdrawal never re-arms it. Refusals are, in order: `stale`,
  `nothing_to_withdraw`, `automatic_hold`, `already_decided` and
  `decision_written`.
- **Revert.** `revert` restores the recorded pre-image of the work's latest
  verified human write of that field, recorded on the work's current case. It is
  an ordinary guarded write: the new plan's `pre_value` is the written value and
  its `desired_value` the pre-image, so the claim re-read and the adapter's
  re-check before its request both require the field still to hold the written
  value. The latest write is the newest plan by (`updated_at`, `plan_id`)
  through the target index; a superseded, stale or abandoned plan wrote nothing
  it kept and is never the latest. Only a write from `accept`, `keep_current`
  or `owner_entered` with no subtitle companion, and whose verified event agrees
  with its plan's pre-image, is revertible; a revert is never itself reverted.
  Cover and description are excluded, and an empty pre-image refuses because
  clearing stays unavailable. Refusals are, in order: `stale`,
  `revert_unavailable`, `revert_no_previous_value`, `revert_field_changed` and
  `already_decided`. Revert is offered while the work has a current case.
- **Holds follow the work (amends ADR-0039).** A manual hold was per case, so
  a successor case inherited none and automatic handling could act on the work
  again after any re-read. Admission now carries the latest active hold per
  destination, with no pending return, from the work's earlier cases to the
  admitted case, keeping its decision as the release token. Holds are still
  created only where an automatic decision exists, and only Return to
  automatic releases one. This also changes holds from `accept` and
  `keep_current`: they persist until the owner returns the field.
- **Amendments.** ADR-0030's exclusion of reversal admits this revert and
  nothing else; bulk editing stays excluded. ADR-0037's rollback and Undo stay
  unavailable: a revert is a new guarded write the owner confirms, never an
  automatic rollback, and clearing stays unavailable. No destination, action
  kind or permission set is added, so the consent envelope is unchanged.

## Consequences

### Positive

- Every field the owner can review has an owner action, and a written mistake
  is recoverable through the same gates as the write that made it.
- Automatic handling no longer resumes on a work merely because it was re-read.

### Negative

- The lost-update window between the adapter's re-read and its request remains;
  Audiobookshelf offers no compare-and-set, as ADR-0039 records.
- Upgrading requires a clean rebuild (ADR-0013): canonical owner decisions
  change their document and outcome check, and no adoption is offered.

### Neutral or deferred

- Recorded deviations from the frozen design: the latest write ignores
  superseded, stale and abandoned plans; a revert's sent value is compared after
  the empty pre-image refusal, so an empty pre-image always reads as
  `revert_no_previous_value`; and the case projection reads carried holds, so a
  successor shows the hold and its Return to automatic control. Library browse
  facts do not yet show a carried hold.
- Reverting an automatic-origin write, clearing a field, adapter
  compare-and-set, and keeping a case current while a revertible write exists
  are follow-up candidates.

## Alternatives considered

### Corrective keep current

It writes back the case target, which after forward re-evaluation is the
written value itself, and only while `keep_current` is offered on that case.

### Holds stay per case

A routine owner write would then let automatic handling act on the successor
case, the defect the P2 probe found already present for any human decision.

## Validation

Unit tests cover the v3 document, the value predicate and suggestion rules,
withdraw and revert eligibility in refusal order, hold carry-forward (a pending
return is not carried, retire then re-admit keeps the hold, Return to automatic
on the successor releases it), and the learning exclusion. Integration tests
write, verify and revert through the guarded pipeline over a fake library.
Vitest and browser journeys cover entry, subset, withdraw and restore, the
stale restore refusal, and unbroken words at every width, theme and motion
setting. The installed idle-load replay counts are unchanged.
