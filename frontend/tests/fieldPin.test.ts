import { describe, expect, it } from 'vitest'

import type { CaseDetailView, DecisionOutcome } from '../src/api/http'
import { fieldDecisionMode, ownerOutcome } from '../src/fieldDecisionMode'
import { cardPin, cardPinned, pinFoot, pinnedCard, underPin, valuePin } from '../src/fieldPin'
import { questionReason } from '../src/fieldReasons'
import { actionable } from '../src/reviewDecisionController'
import { detail } from './fixtures'

// A pin on each value's card (#1346, ADR-0085 Amendment 2).
type Field = CaseDetailView['fields'][number]
const base = detail.fields[0]
if (!base) throw new Error('fixture requires a title field')
const NEW = { kind: 'text', value: 'New title' } as const
const available = { pin: 'available', unpin: false } as const
const pinnedControl = { pin: 'available', unpin: true } as const
const pinOf = (state: NonNullable<Field['pin']>['state'], value: string | null = 'Old title') =>
  ({
    state,
    value:
      value === null
        ? { destination: 'title', state: 'missing', value: null }
        : { destination: 'title', state: 'value', value },
  }) as NonNullable<Field['pin']>
const decided = (
  outcome: DecisionOutcome,
  selected: { kind: string; value: unknown } | null = null,
) =>
  ({
    actor: 'owner',
    application: 'apply',
    application_reason: null,
    decision_id: `decision-${outcome}`,
    destination: 'title',
    occurred_at: 1,
    outcome,
    proposal_id: base.proposalId,
    review_case_id: 'case-1',
    selected_value: selected,
    target_fingerprint: base.targetFingerprint,
  }) as NonNullable<Field['humanDecision']>
const field = (value: Partial<Field> = {}): Field => ({ ...base, pinControl: available, ...value })
const current = { kind: 'current', advice: false, offered: true } as const
const proposed = { kind: 'proposed', value: NEW, advice: false, offered: true } as const
const PINNED_REASON =
  'You pinned this value. Mechanical Librarian will not ask about it or change it unless it changes in Audiobookshelf.'

describe('which card carries the pin', () => {
  it('is the current card in force, the chosen card while waiting and none when changed', () => {
    expect(pinnedCard(field())).toBeNull()
    expect(pinnedCard(field({ pin: pinOf('in_force') }))).toBe('current')
    expect(pinnedCard(field({ pin: pinOf('waiting') }))).toBe('chosen')
    expect(pinnedCard(field({ pin: pinOf('changed') }))).toBeNull()
  })
})

describe('a card that says Pinned', () => {
  it('says so whether or not a pin can be pressed now', () => {
    const held = field({ pin: pinOf('in_force'), pinControl: null })
    expect(cardPinned(held, current)).toBe(true)
    expect(cardPinned(held, proposed)).toBe(false)
    expect(cardPin(held, current)).toBeNull()
    expect(cardPinned(field({ pin: pinOf('changed') }), current)).toBe(false)
    expect(cardPinned(held, { ...current, advice: true })).toBe(false)
  })

  it('shows no automatic decision as the choice of a field pinned without an answer', () => {
    const automatic = { outcome: 'candidate', selected_value: NEW } as NonNullable<
      Field['automaticDecision']
    >
    const pinned = field({ pin: pinOf('in_force'), automaticDecision: automatic })
    expect(underPin(pinned).automaticDecision).toBeNull()
    // An answer of the owner's speaks for the field, and an unpinned field is untouched.
    const answered = { ...pinned, humanDecision: decided('keep_current') }
    expect(underPin(answered)).toBe(answered)
    const plain = field({ automaticDecision: automatic })
    expect(underPin(plain)).toBe(plain)
  })
})

describe('what a press of a card’s pin sends', () => {
  it('unpins from the pinned card', () => {
    const inForce = field({ pin: pinOf('in_force'), pinControl: pinnedControl })
    expect(cardPin(inForce, current)).toEqual({ on: true, press: { kind: 'unpin' } })
    const waiting = field({
      pin: pinOf('waiting', 'New title'),
      pinControl: pinnedControl,
      humanDecision: decided('accept', NEW),
    })
    expect(cardPin(waiting, proposed)).toEqual({ on: true, press: { kind: 'unpin' } })
    const restoring = field({
      pin: pinOf('waiting'),
      pinControl: pinnedControl,
      humanDecision: decided('keep_current'),
    })
    expect(cardPin(restoring, current)).toEqual({ on: true, press: { kind: 'unpin' } })
  })

  it('pins the owner’s chosen card at once', () => {
    expect(cardPin(field({ humanDecision: decided('accept', NEW) }), proposed)).toEqual({
      on: false,
      press: { kind: 'pin' },
    })
    expect(cardPin(field({ humanDecision: decided('keep_current') }), current)).toEqual({
      on: false,
      press: { kind: 'pin' },
    })
    // The chosen card keeps its pin while its own value command is not offered.
    expect(
      cardPin(field({ humanDecision: decided('accept', NEW) }), { ...proposed, offered: false }),
    ).toEqual({ on: false, press: { kind: 'pin' } })
  })

  it('keeps the current value, then pins it', () => {
    expect(cardPin(field(), current)).toEqual({
      on: false,
      press: { kind: 'value_then_pin', outcome: 'keep_current' },
    })
    // Another value is chosen: the current card records Keep current first.
    expect(cardPin(field({ humanDecision: decided('accept', NEW) }), current)).toEqual({
      on: false,
      press: { kind: 'value_then_pin', outcome: 'keep_current' },
    })
    // A changed pin is on no card, so the current card pins the new value.
    expect(cardPin(field({ pin: pinOf('changed'), pinControl: pinnedControl }), current)).toEqual({
      on: false,
      press: { kind: 'value_then_pin', outcome: 'keep_current' },
    })
  })

  it('pins the current value alone where Keep current is not offered', () => {
    expect(cardPin(field({ decisionActions: ['accept'] }), current)).toEqual({
      on: false,
      press: { kind: 'pin' },
    })
  })

  it('chooses an offered value, then pins it', () => {
    expect(cardPin(field(), proposed)).toEqual({
      on: false,
      press: { kind: 'value_then_pin', outcome: 'accept' },
    })
    // A pin in force on the current value moves with the new choice.
    expect(cardPin(field({ pin: pinOf('in_force'), pinControl: pinnedControl }), proposed)).toEqual(
      { on: false, press: { kind: 'value_then_pin', outcome: 'accept' } },
    )
  })

  it('shows no pin where the server offers none', () => {
    expect(cardPin(field({ pinControl: null }), current)).toBeNull()
    expect(
      cardPin(field({ pinControl: { pin: 'change_waiting', unpin: false } }), proposed),
    ).toBeNull()
    expect(cardPin(field({ pinControl: { pin: null, unpin: false } }), current)).toBeNull()
    expect(cardPin(field({ destination: 'cover' }), current)).toBeNull()
    expect(cardPin(field(), { ...proposed, advice: true })).toBeNull()
    expect(cardPin(field(), { ...proposed, offered: false })).toBeNull()
    expect(cardPin(field(), { ...current, offered: false })).toBeNull()
  })
})

describe('the pin beside a value with no card', () => {
  it('pins or unpins the current value', () => {
    expect(valuePin(field(), 'current')).toEqual({ on: false, press: { kind: 'pin' } })
    expect(
      valuePin(field({ pin: pinOf('in_force'), pinControl: pinnedControl }), 'current'),
    ).toEqual({ on: true, press: { kind: 'unpin' } })
    // A changed pin is not on the value the library shows now.
    expect(
      valuePin(field({ pin: pinOf('changed'), pinControl: pinnedControl }), 'current'),
    ).toEqual({ on: false, press: { kind: 'pin' } })
    expect(valuePin(field({ pinControl: null }), 'current')).toBeNull()
    expect(valuePin(field({ destination: 'cover' }), 'current')).toBeNull()
  })

  it('pins or unpins the value the owner entered', () => {
    const entered = field({ humanDecision: decided('owner_entered', NEW) })
    expect(valuePin(entered, 'entered')).toEqual({ on: false, press: { kind: 'pin' } })
    expect(
      valuePin(
        { ...entered, pin: pinOf('waiting', 'New title'), pinControl: pinnedControl },
        'entered',
      ),
    ).toEqual({ on: true, press: { kind: 'unpin' } })
    expect(
      valuePin({ ...entered, pinControl: { pin: 'change_waiting', unpin: false } }, 'entered'),
    ).toBeNull()
  })
})

describe('the sentence at the foot of the field', () => {
  const shown = { pinOnScreen: true }
  it('gives the server’s sentence for a pin in force', () => {
    const inForce = field({
      pin: pinOf('in_force'),
      pinControl: pinnedControl,
      classificationCause: 'pinned',
      classificationReason: PINNED_REASON,
    })
    expect(pinFoot(inForce, shown)).toEqual({ sentence: PINNED_REASON, unpin: false, quiet: false })
    // Unpin sits beside the sentence only where no value on screen carries the pin.
    expect(pinFoot(inForce, { pinOnScreen: false })).toEqual({
      sentence: PINNED_REASON,
      unpin: true,
      quiet: false,
    })
    // Another cause's reason is never passed off as the pin's.
    expect(pinFoot({ ...inForce, classificationCause: 'unsupported' }, shown).sentence).toBe(
      'You pinned this value.',
    )
  })

  it('says a pin on a chosen or entered value starts once the change is written', () => {
    for (const outcome of ['accept', 'owner_entered'] as const)
      expect(
        pinFoot(
          field({ pin: pinOf('waiting', 'New title'), humanDecision: decided(outcome, NEW) }),
          shown,
        ),
      ).toEqual({
        sentence: 'You pinned this value. The pin starts once the change is written.',
        unpin: false,
        quiet: false,
      })
  })

  it('says only that the value is pinned over a restoring Keep current', () => {
    expect(
      pinFoot(field({ pin: pinOf('waiting'), humanDecision: decided('keep_current') }), shown),
    ).toEqual({ sentence: 'You pinned this value.', unpin: false, quiet: false })
  })

  it('says only that the value is pinned over an accept with nothing to write', () => {
    expect(
      pinFoot(
        field({
          pin: pinOf('waiting', 'New title'),
          humanDecision: decided('accept', NEW),
          noChangeRequired: true,
        }),
        shown,
      ),
    ).toEqual({ sentence: 'You pinned this value.', unpin: false, quiet: false })
  })

  it('shows what was pinned, with Unpin, once the value changed', () => {
    expect(pinFoot(field({ pin: pinOf('changed') }), shown)).toEqual({
      sentence: 'You pinned “Old title”.',
      unpin: true,
      quiet: false,
    })
    expect(pinFoot(field({ pin: pinOf('changed', null) }), shown)).toEqual({
      sentence: 'You pinned this field empty.',
      unpin: true,
      quiet: false,
    })
    expect(
      pinFoot(
        field({
          destination: 'language',
          pin: {
            state: 'changed',
            value: { destination: 'language', state: 'value', value: 'en' },
          },
        }),
        shown,
      ).sentence,
    ).toBe('You pinned “English (en)”.')
  })

  it('names a pinned formatted description by its visible text', () => {
    expect(
      pinFoot(
        field({
          destination: 'description',
          pin: {
            state: 'changed',
            value: {
              destination: 'description',
              state: 'value',
              value: '<p>A <b>pinned</b> line</p>',
            },
          },
        }),
        shown,
      ).sentence,
    ).toBe('You pinned “A pinned line”.')
  })

  it('says a recorded change comes first where no pin is offered', () => {
    const waiting = field({ pinControl: { pin: 'change_waiting', unpin: false } })
    expect(pinFoot(waiting, { pinOnScreen: false })).toEqual({
      sentence:
        'A change to this field is recorded. Pin it once Audiobookshelf shows the new value.',
      unpin: false,
      quiet: true,
    })
    // Once the change is written, it waits only for the next scan to read it
    // back, beside Applied and verified.
    expect(
      pinFoot({ ...waiting, writtenByCase: true, changeWritten: true }, { pinOnScreen: false })
        .sentence,
    ).toBe('You can pin this field after the next scan reads its new value.')
    // An earlier write by the case says nothing of a later change still to be
    // written, as Automatic's in a later policy generation.
    expect(pinFoot({ ...waiting, writtenByCase: true }, { pinOnScreen: false }).sentence).toBe(
      'A change to this field is recorded. Pin it once Audiobookshelf shows the new value.',
    )
  })

  it.each(['accept', 'keep_current', 'owner_entered'] as const)(
    'says to choose again where no pin is offered over the owner’s %s',
    (outcome) => {
      // The answer's hold is released or a Resume is asked of it.
      const resumed = field({
        pinControl: { pin: 'change_waiting', unpin: false },
        humanDecision: decided(outcome, outcome === 'keep_current' ? null : NEW),
      })
      expect(pinFoot(resumed, shown)).toEqual({
        sentence:
          'You chose Resume automatic handling for this field. Choose a value again, then pin it.',
        unpin: false,
        quiet: true,
      })
    },
  )

  it('says a recorded change comes first while the owner’s choice of advised text stands', () => {
    const advised = field({
      destination: 'description',
      pinControl: { pin: 'change_waiting', unpin: false },
      humanDecision: decided('keep_current'),
      descriptionAdvice: { selection: { origin: 'human' } } as NonNullable<
        Field['descriptionAdvice']
      >,
    })
    // Recorded but not written, as while writes are off: no scan reads a new
    // value until it is.
    expect(pinFoot(advised, shown).sentence).toBe(
      'A change to this field is recorded. Pin it once Audiobookshelf shows the new value.',
    )
    // An earlier write by the case, as Automatic's, leaves the choice unwritten.
    expect(pinFoot({ ...advised, writtenByCase: true }, shown).sentence).toBe(
      'A change to this field is recorded. Pin it once Audiobookshelf shows the new value.',
    )
    // The choice's own write verified: the next scan is all it waits for.
    const verified = {
      ...advised,
      writtenByCase: true,
      descriptionAdvice: {
        selection: { origin: 'human' },
        application_outcome: 'verified',
      } as NonNullable<Field['descriptionAdvice']>,
    }
    expect(pinFoot(verified, shown).sentence).toBe(
      'You can pin this field after the next scan reads its new value.',
    )
    // Automatic's own advised text is no choice of the owner's.
    const automatic = {
      ...advised,
      descriptionAdvice: {
        selection: { origin: 'automatic_policy' },
      } as NonNullable<Field['descriptionAdvice']>,
    }
    expect(pinFoot(automatic, shown).sentence).toBe(
      'You chose Resume automatic handling for this field. Choose a value again, then pin it.',
    )
    // The same choice on another field's wire is not read.
    expect(pinFoot({ ...advised, destination: 'title' }, shown).sentence).toBe(
      'You chose Resume automatic handling for this field. Choose a value again, then pin it.',
    )
  })

  it('says nothing over an answer that is only history', () => {
    const rejected = field({
      pinControl: { pin: 'change_waiting', unpin: false },
      humanDecision: decided('reject'),
    })
    expect(pinFoot(rejected, shown)).toEqual({ sentence: null, unpin: false, quiet: false })
  })

  it('says nothing where a pin is offered and none stands, or none can be', () => {
    expect(pinFoot(field(), shown)).toEqual({ sentence: null, unpin: false, quiet: false })
    expect(pinFoot(field({ pinControl: null }), shown)).toEqual({
      sentence: null,
      unpin: false,
      quiet: false,
    })
  })
})

describe('how a pinned field reads on the canvas', () => {
  it('asks again for a changed pin, with or without a value to choose', () => {
    const changed = field({
      pin: pinOf('changed'),
      pinControl: pinnedControl,
      classificationCause: 'pin_changed',
      classificationReason: 'Audiobookshelf shows a different value from the one you pinned.',
    })
    expect(actionable(changed)).toBe(true)
    expect(fieldDecisionMode(changed).choiceMode).toBe(true)
    expect(questionReason(changed, 'epub')).toBe(
      'Audiobookshelf shows a different value from the one you pinned.',
    )
    const alone = { ...changed, decisionActions: [] }
    expect(actionable(alone)).toBe(true)
    expect(fieldDecisionMode(alone)).toMatchObject({ actionable: true, choiceMode: false })
    // A pin that is not changed never makes a field ask.
    expect(actionable({ ...alone, pin: pinOf('in_force') })).toBe(false)
    expect(actionable({ ...alone, needsAttention: false })).toBe(false)
  })

  it('keeps the choices of a field pinned from an earlier case', () => {
    const carried = field({
      pin: pinOf('in_force'),
      pinControl: pinnedControl,
      needsAttention: false,
      classification: 'human_recorded',
      classificationCause: 'pinned',
    })
    expect(fieldDecisionMode(carried)).toMatchObject({ revisitable: true, choiceMode: true })
    expect(fieldDecisionMode({ ...carried, editable: false }).revisitable).toBe(false)
    expect(fieldDecisionMode({ ...carried, decisionActions: [] }).choiceMode).toBe(false)
    expect(fieldDecisionMode({ ...carried, pin: null }).revisitable).toBe(false)
  })

  it('settles a pin and an unpin in place', () => {
    expect(ownerOutcome('pin')).toBe(true)
    expect(ownerOutcome('unpin')).toBe(true)
    expect(ownerOutcome('accept')).toBe(false)
    expect(ownerOutcome('keep_current')).toBe(false)
  })
})
