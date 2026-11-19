# ADR-0085: Owner-entered values, withdrawal and reselection

- Status: accepted
- Date: 2026-10-24
- Owners: project maintainers
- Supersedes: none
- Superseded by: none
- Amended by: [ADR-0105](0105-going-back-after-a-write.md) (proposed: the
  owner may clear four fields and enter names, the written value is current on
  the writing case, and a clear holds its field and can be pinned)

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
  supersedes it), and a write that landed before it is not its pre-image: the case reads as
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

## Amendment 2: a pin keeps one field's value and stops the question (2026-11-15)

- Status: proposed. Only the owner's own answer makes this amendment
  accepted; until then the body above and Amendment 1 stay the accepted
  record.

Amendment 1 made every owner answer hold its field against Automatic's
writes. It did not stop the question. A work gets a new case whenever a
source's answer or a library value changes, and on that case a field is asked
again whenever it is Ask me or Automatic abstains, even when the owner kept
the current value on the case before. Issue #1346 asks for an owner
instruction that one field of one work is finished.

- **A pin is an owner decision.** The decision vocabulary gains `pin` and
  `unpin` (`human-decision-v5`), recorded through the field-decision
  operation with every guard it has: the session, CSRF, the current case,
  the target fingerprint, the offer digest and an idempotency key. Neither
  carries a value and neither grants write authority: no planning query,
  pending count or authority reader accepts them. A pin may be recorded on
  any field of the case except the cover, whether or not the field offers a
  choice. It is refused while a change to the field waits to be written, or
  was written and not yet read back by a scan, and no answer of the owner's
  stands on the case, where an answer stands
  whose hold was released or has a Resume asked of it, and on a description
  while the owner's choice of the advised text stands, which a pin would
  otherwise drop before it is written (`pin_change_waiting`). It is refused where the library's value could
  not be read (`unsupported`) and where the field is already pinned at this
  value (`already_pinned`). `unpin` is refused where no pin stands
  (`not_pinned`). Neither row is ever a field's latest decision: every reader
  of the latest decision leaves them out, so an answer under a pin keeps its
  meaning and its write. An answer cannot be withdrawn while its field is
  pinned (`pinned`).
- **The pin lives on the hold.** Where an answer of the owner's stands on the
  case, a pin sets only the `pin` key on that answer's hold and keeps its
  token, so it revokes nothing and the answer's own write goes on. Otherwise
  it sets the hold with the pin decision as its token. The hold document
  (`field-effect-manual-hold-v3`) gains a `pin` key: null, or the
  fingerprint of the pinned value and whether the pin is in force on this
  case. A pin stands only on an active hold with no Resume asked of it. The
  fingerprint hashes the destination, the value and its state,
  with missing, null and empty read as one empty state, so a field pinned
  empty stays pinned however the library stores nothing. The pinned value is
  the chosen value where the standing answer chose one, and the case's value
  otherwise; it is read from the token decision or its case, so nothing
  larger than a digest is stored. No table is added, and the proposal set, case identity, report and
  policy do not depend on pins.
- **In force is a comparison.** Each time admission carries the hold to a
  case, and when a withdrawal restores it, the case's value for the field is
  compared with the pinned fingerprint. While they are equal the pin is in
  force: the field is out of Attention whatever its setting or reason;
  Automatic records no decision for it, and none for a bundle that contains
  it; description advice admits no automatic description; Book matching
  does not ask it again; and the hold refuses every write. Resume automatic
  handling is not offered; Unpin is. A pin on a chosen value is not in force
  until a later case shows that value. Until then the field reads as its
  answer leaves it.
- **A changed value asks once.** When the value differs, after an edit in
  Audiobookshelf, because it could not be read, or because the chosen value
  was never read back, the pin is carried as changed. The field joins Attention with its own reason, even where it
  offers no other value, and shows the pinned value beside the current one.
  Automatic still records nothing and the hold still refuses writes. A new
  `pin` keeps the current value and `unpin` releases the pin. A later read
  that shows the pinned value again puts the pin back in force.
- **Unpin keeps the hold.** `unpin` removes the pin from the case's hold and
  leaves an ordinary hold with the same token, its update time moved to the
  unpin and its row written afresh, so the work's latest hold carries no pin
  to later cases even when a carried copy shares that second. An unpin row
  settles nothing, so the field reads as it would without the pin: decided
  where an answer stands, and open to be asked where none does;
  Automatic stays off it until Resume automatic handling, as for any answer.
- **Another answer ends the pin.** A later `accept`, `keep_current` or
  `owner_entered`, or an owner description choice, replaces the hold without
  a pin. Withdrawing that answer takes back the work's earlier hold as
  Amendment 1 says; a pin on it is compared again with this case's value.
- **Covers are excluded.** An owner cover choice is already kept on every
  later case and never replaced by Automatic.

Consequences. Read-back is still a one-time check after a write: a change
made in Audiobookshelf is seen only at the work's next read. A value that
cannot be read is treated as changed, so the owner is asked rather than the
field held on a value nobody can see. The persisted attention facts gain the
manual holds as an input. Every earlier database needs a clean rebuild
(ADR-0013).

Alternatives considered. Ignoring the field inside `evaluate_report`, as the
`ignore` policy does, would change the case identity on every pin and unpin.
A separate pins table would be a second policy surface beside the hold,
which already follows the work. Treating `keep_current` as a pin stops
writes, not questions. Storing the pinned value on the hold or the decision
would not fit a description in the 4,096-byte hold or the selected-value
column. Treating any direct Audiobookshelf edit as a hold is left to the
owner's decision.

Proof: `tests/unit/test_owner_pins.py`, the pinned-bundle test in
`tests/unit/test_shadow_outcome_persistence.py`, the attention-facts tests in
`tests/unit/test_review_attention_facts.py` and the Book matching tests in
`tests/integration/test_book_matching_fields.py`, each failing before this
amendment. A pin on a chosen value is proved there by
`test_a_pin_on_a_chosen_value_is_accepted_at_every_stage`,
`test_a_pin_on_a_chosen_value_is_in_force_once_read_back`,
`test_a_pinned_choice_never_read_back_asks_with_the_chosen_value`,
`test_a_second_choice_under_a_pin_closes_the_first_choices_write`,
`test_a_pin_after_resume_is_refused_and_revives_nothing`,
`test_withdraw_is_refused_while_pinned`, `test_pin_and_unpin_plan_nothing`
and `test_offer_change_closes_a_pinned_choice`; by
`test_a_pin_never_reopens_a_description_choice` in
`tests/unit/test_description_advice_persistence.py`; by
`test_chosen_value_fingerprint_matches_the_value_read_back` in
`tests/unit/test_human_decision.py`; and by
`test_choose_then_pin_through_the_decision_route` in
`tests/unit/test_review_web_transport.py`.

## Amendment 3: cover Resume where Automatic would switch (2026-11-18)

- Status: proposed. Only the owner's own answer makes this amendment
  accepted. Until then, the body above and Amendments 1 and 2 stay the
  accepted record.

Before this amendment a cover never offered Resume. A cover's automatic
decision is shown only while it is the work's current selection, and an owner
answer makes the owner's selection current. Clearing a cover hold would not
have helped either: the owner's selection still refused every mint.

- **When Resume shows.** The owner has taken a cover over from Automatic
  (Keep, Put back or Others on a switched cover, which holds the field). Later
  the measured stage would switch that work's cover on a newer comparison,
  with every gate passing except the owner's hold or choice. The comparison is
  then marked in the derived table `cover_automatic_held`, and the cover field
  offers Resume while the hold is in force and nothing is pinned. Resume shows
  only at the next case where Automatic decides, never on the comparison the
  owner answered.
- **What Resume does.** It asks for fresh checks, as for any field. When the
  checks complete and the hold clears, the same transaction releases the
  owner's selection pointer that the cleared hold names, and runs admission
  again for the work's current comparison, marked or not. Every cover answer
  made while a cover hold stands takes that hold over, so the hold always
  names the owner's latest answer, and a Resume asked before a newer answer
  clears nothing. Automatic may then switch the cover: the stage, and later
  the taste profile while Switching is Automatic. When the fresh checks find
  nothing to switch, or safety is paused, the cover stays as it is with no
  owner choice over it, and a later check may switch it. That transaction
  reads and writes only the application database.
- **Who never gets it.** A cover chosen in To decide has no hold, so it never
  shows Resume and is never switched. Taste alone never marks a comparison,
  because the judge never looks at an owner-chosen cover.
- **The mark.** A new comparison removes it, and so does each admission of the
  case's current comparison, before the mark is worked out again. An admission
  that stops short, for example while automatic safety is paused, leaves no
  mark. Clearing the hold also removes it. The mark decides only whether
  Resume shows; the release does not need it. A table is used rather than an
  event kind, because events show as Activity rows.
- **Consequence.** After Resume, the owner's earlier pick leaves the taste
  builder's input and "Confirmed {n} of {m}", because both read the current
  pointer.
- **Before #1418.** No stage replacement is admitted, so no comparison is
  marked and Resume never shows in production.

Proof: `test_cover_resume_shows_only_where_the_stage_would_switch_but_for_the_owner`,
`test_cover_resume_hands_the_cover_back_to_automatic` and
`test_a_later_comparison_with_no_switch_clears_the_mark` in
`tests/integration/test_cover_taste_automatic.py`;
`test_a_cover_answer_without_an_automatic_cover_decision_sets_no_hold` in
`tests/unit/test_owner_answer_holds.py` stays green.

## Amendment 4: a held field keeps its hold after Automatic is left and turned on (2026-11-19)

- Status: proposed. Only the owner's own answer makes this amendment
  accepted. Until then, the body above and Amendments 1 to 3 stay the
  accepted record.

Leaving Automatic and turning it on again admits the same case under a new
policy generation, and Automatic's decision of the earlier generation decides
nothing now (ADR-0036). Any owner row on the case then refused the new
decision, so a field the owner's answer held showed neither the hold nor
Resume automatic handling, while the hold still kept Automatic off it. An
answer from an earlier episode of the case read as no answer at all, so the
field read No change, gave a reason that did not apply, and offered no way to
release it. A field already handed back with Resume, its write not yet made,
had nothing left to write.

- **Decided again under the hold.** Where Automatic decided a field on the
  case in an earlier generation and the field has a hold row on the case, in
  force or released by Resume, an owner row no longer refuses Automatic's
  decision in the new generation. A hold in force refuses its write until
  Resume, as on a later case of the work, so the field shows the hold and
  Resume as it did before Automatic was left; a released hold lets it plan.
  This qualifies "Any human row still blocks automatic handling" and "An
  earlier episode's decision still keeps a new automatic decision off that
  field of the case" above.
- **Unchanged elsewhere.** An owner row still refuses the decision where
  Automatic had not decided the field on the case, so an answer given before
  Automatic decided anything there still shows as an ordinary answer until a
  later case (Amendment 1). A pin still refuses it (Amendment 2), and so does
  a withdrawal, which deletes the hold row its answer set or gives the field
  back to the work's earlier hold: a field whose latest answer is a
  withdrawal is not decided again, so a withdrawal never re-arms automatic
  handling.

No stored document changes shape and no version moves; an existing database
gains the decision at the case's next admission.

Proof: `test_a_held_field_keeps_its_hold_and_resume_after_automatic_is_left_and_confirmed`
(current episode, earlier episode, released by Resume), the updated
`test_an_owner_answer_on_an_automatic_field_outlasts_confirming_automatic` and
`test_an_answer_with_no_earlier_automatic_decision_still_keeps_automatic_off`
in `tests/integration/test_leaving_automatic_posture.py`; the first two fail
before this amendment. `test_a_withdrawal_that_restores_an_earlier_hold_still_refuses_automatic`
in `tests/unit/test_owner_answer_holds.py` keeps a withdrawal refusing where it
gives the field back to an earlier hold.

## Amendment 5: a held field says so in Preview (2026-11-19)

- Status: proposed. Only the owner's own answer makes this amendment
  accepted. Until then, the body above and Amendments 1 to 4 stay the
  accepted record.

In Preview, a field whose answer or taken-back pin still holds it read No
change with no line, or with "No source offered a different value." while a
source offered one. Turning on Automatic then left it as it was, and nothing
said why.

- **Held in Preview.** In Preview, a field other than the cover with an
  active hold, no pin and no requested return, which a source offers a
  different value for and which nothing else decides, reads with the cause
  `held_in_preview`: its tab reads Decided and its line says the earlier
  decision holds the field, so turning on Automatic leaves it as it is. This
  qualifies Amendment 1's "Resume shows only where it can act" and Amendment
  2's "open to be asked where none does": the field shows that it is held,
  but `manual_hold` stays false, so Resume automatic handling is still
  neither shown nor accepted. Needs you and Optional decide first, as in
  Automatic.
- **Unchanged elsewhere.** Outside Preview nothing changes: in Automatic the
  hold shows with Resume where Automatic decided the field, and otherwise the
  field reads No change.
- **Activity.** An automatic choice that an active hold without a pin or a
  requested return keeps unwritten reads "Held by you" while Automatic is on.
  That includes an automatic description, and both halves of a split title and
  subtitle when either is held, since the pair is written together. A choice
  with nothing left to write, or whose write was stopped for the owner, reads
  no note.

No stored document changes shape; `review-case-detail-v15` and
`continuous-field-activity-v5` carry the new cause and note. No clean rebuild.

Proof: `test_preview_shows_an_earlier_choice_only_where_automatic_decides_again`
and `test_a_pin_taken_back_in_preview_holds_a_field_automatic_never_decided` in
`tests/integration/test_leaving_automatic_posture.py`, and
`test_a_pin_taken_back_in_preview_reads_decided_with_its_hold_line` in
`tests/browser/test_review_browser.py`; each fails before this amendment.

## Amendment 6: a pin taken back leaves a hold Automatic decides under (2026-11-19)

- Status: proposed. Only the owner's own answer makes this amendment
  accepted. Until then, the body above and the earlier amendments stay the
  accepted record.

Amendment 2 keeps the hold after Unpin, so Automatic stays off the field until
Resume automatic handling. Where the owner pinned a field and took the pin back
before Automatic had decided it on the case, as on a work first prepared in
Preview, the pin and unpin rows refused Automatic's decision once it was on.
With no decision to hold, the field read No change with no reason that
applied and offered no Resume, while the hold still kept Automatic off it. The
owner could not see the hold or release it until a later case of the work.

- **Decided under the hold.** Where every owner row of the field on the case
  is a `pin` or an `unpin` and the field has a hold row on the case, in force
  or released by Resume, those rows no longer refuse Automatic's decision. A
  hold in force refuses its write until Resume, so the field shows the hold
  and Resume on this case as on a later case of the work; a released hold lets
  it plan. Activity notes the held choice "Held by you", as Amendment 5
  describes. This qualifies Amendment 2's "open to be asked where none does"
  for a field Automatic decides, Amendment 4's "A pin still refuses it" for a
  pin taken back, and Amendment 5's "otherwise the field reads No change" for
  such a field. Preview is unchanged: the field still reads held there, and
  turning on Automatic leaves its value as it is.
- **Unchanged elsewhere.** A pin in force or changed still refuses the
  decision (Amendment 2). An answer still refuses it where Automatic had not
  decided the field on the case, so it shows as the owner's answer until a
  later case (Amendment 1), and a withdrawal still refuses it (Amendment 4).

No stored document changes shape and no version moves; an existing database
gains the decision at the case's next admission.

Proof: `test_a_pin_taken_back_before_automatic_decided_shows_the_hold_once_it_is_on`
in `tests/integration/test_leaving_automatic_posture.py` (unpinned fails before
this amendment; pinned keeps a pin in force refusing; it also checks the
Activity note), `test_a_pin_taken_back_in_preview_holds_a_field_automatic_never_decided`
in the same file (now decided under the hold once Automatic is on),
`test_a_title_pin_taken_back_in_preview_holds_the_whole_bundle` in the same
file (an audiobook's title and subtitle are decided as one bundle under the
title's hold), and
`test_a_pin_taken_back_in_preview_shows_the_hold_once_automatic_is_on` in
`tests/browser/test_review_browser.py`.
