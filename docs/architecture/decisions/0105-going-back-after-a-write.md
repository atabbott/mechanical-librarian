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
   that write as the field's current value (a write is in the episode when it
   landed after the case was last admitted, whichever episode planned it): it is refused as unchanged, and
   `owner_control.current` carries it. A latest write is always the one that
   landed last, whichever decision was recorded first. On any case, "Use
   previous value", the previous names and genres marked Previous are offered
   only while the field reads the latest verified write's desired value, and
   offer the value that
   write replaced. An empty replaced value is never offered: emptying the
   input is that choice, and the server sends no empty suggestion. On that
   case Keep current writes back the value the case read: a clearable field
   it read empty goes back to empty as a clear does, and any other field
   refuses the keep as `owner_value_required`. A description it read stored
   with formatting a restore would lose refuses the keep as
   `restore_unavailable` (#1424). The case detail's `written_by_case` marks
   each field the case wrote in its current episode; a description written
   with the adviser's text counts, by the same rule as the write a keep
   follows. On a field the case wrote the browser shows that keep as a card
   headed "Before your change", holding the value the case read, and never
   calls it current. It leaves the card out where owner entry already offers
   the same way back (Clear for an empty value, or that value as the previous
   value, names or genres), and where there is none, a field read empty that
   cannot be emptied or a formatted description (the detail's
   `restore_unavailable`), saying so in one sentence. A field whose latest
   write holds the value the case read again, after a keep, a clear or an
   entry, is not written: after a keep its card is Keep current value, and
   after a clear or an entry of that value, in the same order, the browser
   leaves the card out because the recorded value already holds it. History
   reads a keep that goes back as "You went back to the value before your
   change.", its To an empty value as Not set, and Activity reads it as a
   change from the written value to that value, from the keep's own first
   plan, or before one exists from the write it follows. Every owner decision
   freezes, when it is recorded, the write it follows; a keep goes back on it.
   The stored decision names the decision of the write planning resolves for
   it then, or null when it follows none, or, for a keep, when that write
   already holds the value the case read, which makes the keep a plain keep.
   While the owner decision that made that write is still the field's answer
   and its own hold holds the field, an accept of the value it holds is
   refused as `owner_value_unchanged` and records nothing, since planning
   would write nothing for it. On any other field than the cover, where that
   write's decision is no longer the field's answer or Resume automatic
   handling released its hold or was asked to, or the write is Automatic's or
   the description adviser's, such an accept is the owner's answer with
   nothing to write: it is recorded with an owner row in
   `field_effect_no_changes`, holds the field, replaces any unwritten answer
   after that write and is never pending. The case detail marks the field
   `no_change_required`. An accept of the same value while that answer stands
   and holds the field is refused the same way. The pending set holds a keep
   only while it names a write. History's From for every decision, and
   Activity's written value for a keep, read that write's own verified effect,
   never episode floors or a clock in whole seconds, so a later return or a
   write in the same second changes none of them. Its result is Went back once
   that write back verifies; until then it is the write's own word (Planned,
   Closed and so on, with the reason), or Recorded while unplanned, and never
   Kept current. Activity's shared fact store re-reads an owner decision's row
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
   until the first scan once its 30 days have passed, unless a requested
   refresh, a changed file or a recovery reset first prepares the work and
   identifies nothing. That retires the kept case as it retires any case
   (ADR-0031, #1953): the write it was kept for is already verified, so
   nothing about the write is lost, only the way back while the work stays
   unidentified. Once preparation identifies the work again within the 30
   days, admission keeps the work's case again for the same write, as a new
   admission, and offers the way back again. A scheduled search that
   identifies nothing still keeps it (ADR-0102).
7. **Payload forms.** The adapter sends `null` for subtitle and publisher and
   `[]` for genres and narrators, and reads `null`, `""`, an absent key and
   `[]` as empty on every field but the explicit flag. Read-back alone shows
   a wrong form, so the adapter's contract tests pin the form per field. No
   clear is refused as unsupported on the pinned versions; that refusal stays
   for any field outside the clearable set.
8. **Versions.** `human-decision-v7` (#1973; v6 named a write on a keep
   only), `review-case-detail-v10`, `review-case-history-v5` and the
   `audiobookshelf-field-effect-v4` qualification receipt.
   `review_case_transitions` admits `recent_write` in both of its `CHECK`s,
   and `review_case_episodes` floors an episode by field-effect event
   (`event_floor`). A clean rebuild is required (ADR-0013).

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
- A case kept only for a changed pin stays current once the owner unpins
  or answers, as any answered case does, until the work next changes
  (Amendment 1).
- While a re-preparation identifies nothing, a written work has no case to
  go back from; the way back returns when the work is identified again
  within the 30 days. An owner answer recorded on the kept case but not yet
  sent when it retires is refused, as on any retired case.
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

## Amendment 1: a changed pin keeps the case open (#2035)

A pinned field whose value changed asks however little else is left to
decide, but a work whose scan found nothing actionable lost its case, and the
pin was never asked: when its kept case expired, or when it had no recent
write at all. Admission now keeps the case while a pin the work carries would
read changed on it, compared as the hold's carry compares it (ADR-0085,
Amendment 2):

- With a recent write the case is kept as above, with the reason
  `recent_write`. Once the write is 30 days old the same ProposalSet replays
  while the pin still reads changed, and retires at the first scan after it
  no longer does.
- Without one the case is admitted with the reason `actionable`, on any
  admission, with or without the scan's time.
- Either way it is a kept case: preparation asks no cover or description
  advice for a current case whose ProposalSet has nothing actionable and
  does not project it again for a pending request, and it adds no attention
  fact except the changed pin.

No table, `CHECK` or document version changes, so no clean rebuild is needed.
A work that retired with a changed pin before this amendment is asked once
the work next changes.

## Validation

Persistence tests cover the empty kind, names and their refusals, both new
`CHECK`s, pinning a clear, the written value as current, when previous is
offered, the kept case at 29 and 30 days with replay, an actionable successor,
a changed pin, the skipped advice, the unchanged-work expiry, and a kept case
retired by a re-preparation that identifies nothing, with its verified write
unchanged, an unsent answer on it refused, and the case kept again once the
work is identified again. Adapter tests pin each payload and read form. The
Audiobookshelf field-effect qualification enters, goes back on and clears
each field on every pinned image, checks each clear raw, and on the installed
path clears a written subtitle, pins the clear and checks that the next scan
keeps it quiet.
