# ADR-0085: Owner-entered values, withdrawal and reselection

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
and a written decision could be undone only by a corrective `keep_current` on
the same case, which writes back the case target rather than what the write
replaced (#1285). Every guarded write already records the value it read back
immediately before writing (`pre_value`), in its plan and in every append-only
event, so the value a write replaced is durable.

## Decision

- **Vocabulary.** Human decisions move to `human-decision-v3`, with outcomes
  `accept`, `keep_current`, `reject` (historical), `owner_entered` and
  `withdraw`. The document's keys are unchanged; the outcomes join the request
  digest as before. `decision_actions` is unchanged, so the attention entry
  predicate, consent and policy are too.
- **Owner-entered values.** Title, subtitle and publisher take owner text
  through `validation.owner_text_value`: 1 to 512 code points, already NFC with
  whitespace collapsed, and no control, path, credential or URI text. The server
  refuses a value that fails (`owner_value_invalid`) or equals the current one
  (`owner_value_unchanged`); it never rewrites what was sent. Suggestions
  (`title_case`, `strip_part`, `split_author`, `combined`, and `previous` under
  Reselection) are pure, at most five, read only the current value or, for
  `previous`, the value before the latest write, and only fill the input.
  Genres take any non-empty subset, of at most 32, of the union of current,
  eligible proposed and previous genres, in union order. Bulk editing and every other destination
  stay excluded. No proposal, shadow, automatic-decision, policy or
  qualification path reads an owner-entered value or a previous value.
- **Withdraw.** `withdraw` appends a row with no value and revokes the
  decision's unclaimed authority in the same transaction. A field is decided
  while its latest row is anything but `withdraw`, so a withdrawn actionable
  field returns to Attention. Any human row still blocks automatic handling, so
  a withdrawal never re-arms it. Refusals are, in order: `stale`,
  `nothing_to_withdraw`, `automatic_hold`, `already_decided` and
  `decision_written`.
- **Reselection.** Going back to an earlier value is choosing it again. For
  title, subtitle and publisher, the value the field held before its latest
  verified write, of any origin, is one more suggestion, `previous`, labelled
  Previous value; for genres, previous genres not already listed join the
  subset options tagged Previous. The read is bounded: the latest verified plan
  per destination through the target index, ordered by when its verified
  event was appended, which a one-second `updated_at` cannot settle, so a
  superseded, stale or abandoned plan is never treated as the latest write. A subtitle written as a title's companion is
  shadowed by that title write; a verified event that disagrees with its plan,
  or an empty or invalid pre-image, offers nothing; cover and description
  offer nothing. A suggestion grants no authority: choosing it is an ordinary
  `owner_entered` write under every existing guard, so a field changed since
  refuses as `stale`, and the server admits a previous genre only while that
  same pre-image is still the latest. There is no revert outcome, plan
  reference, refusal code, route or confirmation.
- **Holds follow the work (amends ADR-0039).** A manual hold was per case, so
  a successor case inherited none and automatic handling could act on the work
  again after any re-read. Admission now carries the latest active hold per
  destination, with no pending return, from the work's earlier cases to the
  admitted case, keeping its decision as the release token. Holds are still
  created only where an automatic decision exists, and only Resume automatic
  handling releases one. This also changes holds from `accept` and
  `keep_current`: they persist until the owner returns the field. A case whose
  hold was carried shows the hold, not its paused automatic decision, as what
  speaks for the field. A hold never refuses an automatic decision, only the
  write planned from it: title recovery re-projects a held successor, and
  automatic description admission records its authored selection, so the
  case shows the hold, and Resume, once there is a decision for it to hold.
  Until Resume that decision is neither pending nor shown as chosen.
- **Amendments.** ADR-0030's exclusion of reversal stays: nothing reverses a
  write, and an earlier value returns only as a new owner-entered decision;
  bulk editing stays excluded. ADR-0037's rollback and Undo stay unavailable,
  and clearing stays unavailable. No destination, action kind or permission set
  is added, so the consent envelope is unchanged.

## Consequences

### Positive

- Every field the owner can review has an owner action, and a written mistake
  is recoverable through the same gates as the write that made it, without a
  second write path or a second set of refusals.
- Automatic handling no longer resumes on a work merely because it was re-read.

### Negative

- Fields without owner entry, such as authors, return to an earlier value only
  when a provider proposes it.
- The lost-update window between the adapter's re-read and its request remains;
  Audiobookshelf offers no compare-and-set, as ADR-0039 records.
- Upgrading requires a clean rebuild (ADR-0013): canonical owner decisions
  change their outcome check, and no adoption is offered.

### Neutral or deferred

- Recorded deviation from the frozen design: the case projection reads carried
  holds, so a successor shows the hold and its Resume automatic handling
  control. Library browse facts do not yet show a carried hold.
- A case id is a content hash, so a work that returns to exactly the content of
  an earlier case re-admits that case, and the re-admission starts a new
  episode of it. Its earlier owner decisions are append-only and stay as
  history, but they settle, word, withdraw, plan and authorize nothing in the
  new episode (a plan made for one never applies, and the next owner decision
  supersedes it), and a write planned before it is not its pre-image: the case reads as
  undecided until the owner decides again. Choosing the previous value again
  therefore leaves the earlier case reading as new, not as "You entered X"
  marked written. An earlier episode's decision still keeps a new automatic
  decision off that field of the case, so a recurrence never hands a field the
  owner decided back to automatic handling.
- Leaving Automatic with the policy unchanged retires and re-admits the same
  case with nothing about it changed, so that re-admission continues the
  case's current episode instead of starting a new one: its owner decisions,
  holds and queued writes stay current.
- The re-admitted case's own hold row takes the work's latest hold state, a
  release or pending return included. Where that case has no automatic
  decision, a new owner decision there removes its released row, so the
  decision plans and nothing carries forward, as on a case never held. Resume
  is accepted only where the case shows the hold holding a decision, through
  the API as through its control, so a removed row is only ever a copy of a
  release still recorded elsewhere and a released hold never resurrects. A
  field reads as held only while its hold row is in force, so Return to
  automatic clears the held flag on the case that set it too.
- Extending owner entry to more fields, clearing a field and adapter
  compare-and-set are follow-up candidates.

## Alternatives considered

### Guarded revert

A separate `revert` outcome naming the plan it reverts, with its own
eligibility, refusals and confirmation, was built and dropped by the owner:
the same outcome is reached by choosing the earlier value again, with one
write path and the canvas unchanged.

### Corrective keep current

It writes back the case target, which after forward re-evaluation is the
written value itself, and only while `keep_current` is offered on that case.

### Holds stay per case

A routine owner write would then let automatic handling act on the successor
case, the defect the P2 probe found already present for any human decision.

## Validation

Unit tests cover the v3 document, the value predicate and suggestion rules,
withdraw eligibility in refusal order, the previous-value read (after a write,
of any origin, never from a stale plan, a disagreeing event, an empty pre-image
or a description), previous genres, hold carry-forward (a pending return is not
carried, retire then re-admit keeps the hold, Resume automatic handling on the
successor releases it), a re-admitted case as a new episode (the previous value
chosen again, a decision on the re-admitted case, a later successor that
recurs), and the learning exclusion. Integration tests write,
verify and choose the previous value again through the guarded pipeline over a
fake library. Vitest and browser journeys cover entry, subset, withdraw, the
previous value, a stale reselection, and unbroken words at every width, theme
and motion setting. The installed idle-load replay counts are unchanged.

## Amendment 1: every owner answer holds its field (2026-11-15)

- Status: proposed. Only the owner's own answer makes this amendment
  accepted; until then the body above stays the accepted record.

An audit of the automatic decision paths found that an owner's answer was not
kept once Automatic could act on its field. A hold was created only where an
automatic decision sat on the same case as the answer. After Automatic's write
was read back the work had a new case with no automatic decision on it (for
the fields decided by the scalar engine, such as the title, the read-back is
an abstention), so the owner's correction there, a typed value or Previous
value, set no hold, and Automatic wrote its own value again on the read-back
or the next changed read. The same rule lost answers given in Preview, while
automatic decisions were paused, and while the field was Ask me and later set
to Automatic. That contradicts the product rule that the owner's decision on
a field always overrides Automatic for that field
(`docs/product/automation-policy.md`).

- **Every answer holds.** Every owner decision except a withdrawal, whatever
  its outcome (`accept`, `keep_current` or `owner_entered`) and whatever the
  mode or field setting when it was given, sets the field's manual hold on
  its case, with that decision as the release token, whether or not an
  automatic decision is on the case. Cover is the exception: a cover answer
  still holds only over an automatic cover decision on its case, and with
  none it removes a released cover row the case carries, as before, so it
  plans. A held cover refuses an automatic replacement decision outright, so
  a later case would never show Resume to release it. Automatic cover
  replacement is not qualified today, so no cover answer is overwritten; the
  automatic cover switching planned under #1372 must keep an owner's cover
  choice. The hold follows the work as before, so Automatic still records
  its decisions on later cases but plans no write
  from them until the owner chooses Resume automatic handling. This replaces
  "Holds are still created only where an automatic decision exists" above,
  and, on a re-admitted case, "a new owner decision there removes its
  released row, so the decision plans and nothing carries forward": the new
  answer's hold replaces the released row, the answer still plans, and its
  hold carries forward.
- **A withdrawal holds nothing.** It sets no hold, and the hold the withdrawn
  answer set goes with it: the case takes back the work's latest hold from
  its earlier cases if that hold is still in force and has no return
  pending, and otherwise holds nothing. A hold the withdrawn answer did not
  set stays. A withdrawal is still refused on a case with an automatic
  decision (`automatic_hold`).
- **Resume shows only where it can act.** Nothing on the case page changes:
  a field shows the hold, and Resume automatic handling, only where the case
  has an automatic decision for the hold to hold, which happens only while
  the field is Automatic and Automatic is on. An answer given under Ask me,
  in Preview or while paused therefore shows as an ordinary answer until a
  later case of the work has an automatic decision.
- **Answers recorded before this amendment** carry no hold where they set
  none. No stored document changes shape and no version moves; the clean
  rebuild already planned leaves no such answer behind.

Book matching's `matching_changed` row is not an owner answer and leaves the
hold untouched, as before. Proof: `tests/unit/test_owner_answer_holds.py`,
whose title, genres, Preview, pause, Ask me, withdrawal and Resume cases each
failed before this amendment, and the recurring-case tests in
`tests/unit/test_owner_command_persistence.py`.
