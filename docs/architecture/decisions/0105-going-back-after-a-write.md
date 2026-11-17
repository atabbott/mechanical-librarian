# ADR-0105: Going back after a write

- Status: proposed
- Date: 2026-11-16
- Owners: project maintainers
- Issues: #1523
- Amends: ADR-0085 (owner values: a clear, names, and the written value as
  current), ADR-0014 (a kept case), ADR-0031 (the unchanged-work branch) and
  ADR-0013 (one more clean rebuild)
- Supersedes: none
- Superseded by: none

## Context

ADR-0085 let the owner go back by choosing the value a write replaced. Four
gaps stopped that working when it mattered:

- The case that wrote a field still read the value from before the write, so
  "Use previous value" was not offered there and the written value could be
  entered again as a change.
- A scan after the write usually found nothing left to decide and retired the
  case, so the work left Library with nothing to go back from.
- A subtitle, publisher, genres or narrators could not be emptied: the plan
  refused an empty desired value and the adapter refused empty read forms.
  Authors and narrators took no owner value at all.
- A chosen empty value could not be pinned.

A probe on the four pinned Audiobookshelf versions (2.26.0, 2.36.0, 2.37.1,
2.37.2) found the same behaviour on each. `null` empties a subtitle or
publisher and `[]` empties genres or narrators; the other form answers 200 and
changes nothing. An emptied scalar reads back as `null` and an emptied list as
`[]`, and survives a restart.

## Decision

1. **A clear is an owner value.** The owner's empty value is one closed kind,
   `{"kind":"empty","value":null}`, accepted only on `owner_entered` and only
   for subtitle, publisher and genres on both media and narrators on
   audiobooks. Title and authors refuse it as `owner_value_required`, because
   identity admission and provider searches read them. An
   empty text or list is `owner_value_invalid`: only the closed kind clears.
   Clearing a field that is already empty is `owner_value_unchanged`. A plan
   carries an empty desired value only for a clearable field under the owner's
   authority, so `field-effect-plan-v1` does not move. `human-decision-v6`
   admits the kind, and two `human_decisions` `CHECK`s hold it: a selected
   value is null or one closed kind, and `empty` carries no value and appears
   only on `owner_entered`.
2. **A clear holds and can be pinned.** A clear is an ordinary owner answer.
   It ends any pin on the field, as every later answer does under ADR-0085
   Amendment 2, and holds the field under Amendment 1. Its chosen-value
   fingerprint is `{"state":"empty","value":null}`, the state an empty field
   is pinned in, so the owner can pin the clear. Once it reads back empty, a
   scan in which a source still offers a value keeps the field held, pinned
   and out of Attention.
3. **Names.** Authors on both media and narrators on audiobooks take 1 to 32
   names, each within the owner-text rules, with no repeat, in the owner's
   order; names equal after NFC and case folding are a repeat. Entry opens
   with the current names. It reaches only primary,
   non-ineligible rows under F59's primary-row rule; a field no source returns
   stays contextual and has no owner control. An empty narrators list is a
   clear.
4. **The written value is current.** On the case whose own latest verified
   write in its episode set a field, owner entry treats the desired value of
   that write as the field's current value: it is refused as unchanged, and
   `owner_control.current` carries it. On any case, "Use previous value", the
   previous names and genres marked Previous are offered only while the field
   reads the latest verified write's desired value, and offer the value that
   write replaced. An empty replaced value is never offered: emptying the
   input is that choice, and the server sends no empty suggestion. On that
   case Keep current writes back the value the case read: a clearable field
   it read empty goes back to empty as a clear does, and any other field
   refuses the keep as `owner_value_required`. The case detail's
   `written_by_case` marks each field the case wrote in its current
   episode. There the browser shows that keep as a card headed "Before your
   change", holding the value the case read, and never calls it current. It
   leaves the card out where owner entry already offers the same way back
   (Clear for an empty value, or that value as the previous value, names or
   genres), and where there is none, a field read empty that cannot be
   emptied, saying so in one sentence. History reads that keep as "You went
   back to the value before your change.", its To an empty value as Not set,
   and Activity reads it as a change from the written value to that value,
   from the keep's own first plan, or before one exists from the write it
   follows. That write is frozen when the keep is recorded: the stored keep
   names the decision of the write planning resolves for it then, or null
   when it follows none, and History's From and Activity's written value
   read that write's own verified effect, never episode floors, so a later
   return changes neither. Its result is Went back once that write back
   verifies; until then it is the write's own word (Planned, Closed and so
   on, with the reason), or Recorded while unplanned, and never Kept
   current. Activity's shared fact store re-reads an owner decision's row
   when its own effects or the named write's effects move (ADR-0082). The
   server path and its refusal stay for API callers.
5. **The kept case.** When the pipeline admits a ProposalSet with nothing
   actionable while the work's latest verified write, of any origin and
   destination, covers included, is under 2,592,000 seconds old by the
   pipeline's clock, admission keeps the case. The transition is `admitted`
   or `superseded` with the reason `recent_write`, and holds and pins carry
   as for any case. Only the pipeline passes the time; a command-line
   admission retires as before. A kept case adds no attention fact, count or
   lens, except a pinned field whose value changed, which asks as on any
   case. It takes no part in cover consideration or description advice;
   its shadow outcome and level are reconciled as for any case. An
   actionable result replaces it with the reason `actionable`.
6. **Expiry.** Admission with the time does not replay a `recent_write` case
   once the write is 30 days old, so the same ProposalSet retires it. The
   unchanged-work branch of preparation sends such a work back to admission
   when the scan's time is 30 days past the write. A kept case therefore lasts
   until the first scan once its 30 days have passed.
7. **Payload forms.** The adapter sends `null` for subtitle and publisher and
   `[]` for genres and narrators, and reads `null`, `""`, an absent key and
   `[]` as empty on every field but the explicit flag. Read-back alone shows
   a wrong form, so the adapter's contract tests pin the form per field. No
   clear is refused as unsupported on the pinned versions; that refusal stays
   for any field outside the clearable set.
8. **Versions.** `human-decision-v6`, `review-case-detail-v10`,
   `review-case-history-v5` and the `audiobookshelf-field-effect-v4`
   qualification receipt. `review_case_transitions` admits `recent_write` in
   both of its `CHECK`s. A clean rebuild is required (ADR-0013).

## Consequences

### Positive

- The owner can go back straight after a write, from the case that wrote it,
  and for 30 days afterwards from the work's kept case.
- Four fields can be emptied, and authors and narrators can be corrected by
  name, under every effect guard and the read-back.
- A cleared field stays cleared: the hold keeps Automatic off it, and a pin
  stops a source that still offers a value from asking again.

### Negative

- Under Ask me, a cleared field that a source still fills asks again unless
  the owner pins the clear.
- Any later verified write, a cover included, restarts the 30 days, and a
  kept case can outlive them until the next scan.
- When a kept case expires, a pinned field whose value changed leaves
  Attention with it, unasked, as a retired case's does today.
- A wrong empty payload succeeds silently at the service; only read-back
  catches it.

### Neutral or deferred

- Kept cases count in Library as works with a case and nothing to decide.

## Alternatives considered

### A confirmation strip or a dialog for a clear

An inline strip like Settings' Clear, or a dialog, adds a second press to an
action the owner can go back on. The owner chose one press: the button reads
Clear subtitle, or the field's name, with no explanatory line.

### An empty previous suggestion

"Use previous value, which was empty" would offer a second control for the
same act as emptying the input. It was dropped; the server sends no empty
suggestion.

### A clear that moves or refuses a pin

Moving a pin to the empty value on a clear, or refusing a clear while pinned,
would treat a clear unlike every other answer. A clear ends the pin, and the
owner pins the clear if wanted.

### Keeping the case without a time

Keeping every written work's case for good would fill Library with works
that have nothing to decide and never leave. Thirty days from the latest verified write covers going back after a review
session without that cost.

## Validation

Persistence tests cover the empty kind, names and their refusals, both new
`CHECK`s, pinning a clear, the written value as current, when previous is
offered, the kept case at 29 and 30 days with replay, an actionable successor,
a changed pin, the skipped advice and the unchanged-work expiry. Adapter tests
pin each payload and read form. The Audiobookshelf field-effect qualification
enters, goes back on and clears each field on every pinned image, checks each
clear raw, and on the installed path clears a written subtitle, pins the clear
and checks that the next scan keeps it quiet.
