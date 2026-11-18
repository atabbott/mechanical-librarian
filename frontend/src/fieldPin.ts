// An owner pin on one field (ADR-0085, Amendment 2): which card carries it,
// what a press of a card's pin sends, and the sentence at the foot of the
// field. The server says whether a pin is accepted now; nothing here guesses.
import { targetValueText } from './api/http'
import type { DecisionCommand } from './api/http'
import { visibleText } from './descriptionFormatting'
import { keepOffered, valueText } from './fieldDecision'
import type { ReviewField } from './fieldDecision'
import { humanChoiceSelected } from './reviewAutomaticSelection'

export type PinPress =
  | { kind: 'pin' }
  | { kind: 'unpin' }
  | { kind: 'value_then_pin'; outcome: 'accept' | 'keep_current' }
export interface PinCard {
  kind: 'current' | 'proposed'
  value?: DecisionCommand['selectedValue']
  // The description adviser's cards record through another command.
  advice: boolean
  // The card's own value command would be accepted now.
  offered: boolean
}

// A pin in force is on the value the library shows; a waiting pin is on the
// value the owner chose; a changed pin is on no card.
export const pinnedCard = (field: ReviewField) =>
  field.pin?.state === 'in_force' ? 'current' : field.pin?.state === 'waiting' ? 'chosen' : null
const pinOffered = (field: ReviewField) =>
  field.destination !== 'cover' && field.pinControl?.pin === 'available'
const ownerChose = (field: ReviewField, card: PinCard) =>
  humanChoiceSelected(
    field.humanDecision,
    card.kind === 'current' ? 'keep_current' : 'accept',
    card.kind === 'current' ? null : card.value,
  )

// The card says Pinned whether or not a pin can be pressed now.
export const cardPinned = (field: ReviewField, card: PinCard) => {
  const pinned = pinnedCard(field)
  return (
    !card.advice &&
    (pinned === 'current'
      ? card.kind === 'current'
      : pinned === 'chosen' && ownerChose(field, card))
  )
}

export const cardPin = (
  field: ReviewField,
  card: PinCard,
): { on: boolean; press: PinPress } | null => {
  if (!pinOffered(field) || card.advice) return null
  if (cardPinned(field, card)) return { on: true, press: { kind: 'unpin' } }
  if (ownerChose(field, card)) return { on: false, press: { kind: 'pin' } }
  if (!card.offered) return null
  if (card.kind === 'proposed')
    return { on: false, press: { kind: 'value_then_pin', outcome: 'accept' } }
  return {
    on: false,
    press: keepOffered(field)
      ? { kind: 'value_then_pin', outcome: 'keep_current' }
      : { kind: 'pin' },
  }
}

// The pin beside a value that has no card: the current value of a field with
// nothing to choose, or the value the owner entered.
export const valuePin = (
  field: ReviewField,
  value: 'current' | 'entered',
): { on: boolean; press: PinPress } | null => {
  if (!pinOffered(field)) return null
  const on = value === 'current' ? field.pin?.state === 'in_force' : field.pin !== null
  return { on, press: { kind: on ? 'unpin' : 'pin' } }
}

// While a pin stands with no answer of the owner's on the case, Automatic
// decides nothing for the field, so a decision it made before the pin is not
// shown as the field's choice.
export const underPin = (field: ReviewField): ReviewField =>
  field.pin !== null && field.humanDecision === null && field.automaticDecision !== null
    ? { ...field, automaticDecision: null }
    : field

// A description in a sentence reads as its visible text, not its markup.
const pinnedText = (destination: ReviewField['destination'], text: string) =>
  destination === 'description' ? visibleText(text) : valueText(destination, text)
const pinnedValueSentence = (field: ReviewField) => {
  const value = field.pin?.value
  return value?.state === 'value'
    ? `You pinned “${pinnedText(field.destination, targetValueText(value.state, value.value))}”.`
    : 'You pinned this field empty.'
}

// Why a field that could be pinned offers no pin now. With no answer of the
// owner's standing, or with the owner's choice of the adviser's description,
// a change waits to be written. Over a standing answer the server refuses
// only once that answer's hold was released or a Resume was asked of it. A
// rejection is history the owner can no longer record, so nothing is said.
const notOffered = (field: ReviewField) =>
  field.humanDecision === null ||
  (field.destination === 'description' && field.descriptionAdvice?.selection?.origin === 'human')
    ? 'A change to this field is recorded. Pin it once Audiobookshelf shows the new value.'
    : field.humanDecision.outcome === 'reject'
      ? null
      : 'You chose Resume automatic handling for this field. Choose a value again to pin it.'

// What a failed unpin says where the foot's own Unpin is the control to press.
export const STILL_PINNED = 'This field is still pinned. Press the pin again.'
const STILL_PINNED_AT_FOOT = 'This field is still pinned. Press Unpin again.'
export const footAlert = (said: string, unpinAtFoot: boolean) =>
  unpinAtFoot && said === STILL_PINNED ? STILL_PINNED_AT_FOOT : said

// The sentence at the foot of the field and whether Unpin sits beside it:
// only where no card or value on screen carries the pin.
export const pinFoot = (
  field: ReviewField,
  { pinOnScreen }: { pinOnScreen: boolean },
): { sentence: string | null; unpin: boolean; quiet: boolean } => {
  const pin = field.pin
  if (pin === null) {
    const sentence = field.pinControl?.pin === 'change_waiting' ? notOffered(field) : null
    // Why no pin can be pressed is an explanation, not a state of the field.
    return { sentence, unpin: false, quiet: sentence !== null }
  }
  if (pin.state === 'changed')
    return { sentence: pinnedValueSentence(field), unpin: true, quiet: false }
  return {
    quiet: false,
    sentence:
      pin.state === 'in_force'
        ? field.classificationCause === 'pinned' && field.classificationReason
          ? field.classificationReason
          : 'You pinned this value.'
        : field.humanDecision?.outcome === 'keep_current'
          ? 'You pinned this value.'
          : 'You pinned this value. The pin starts once the change is written.',
    unpin: !pinOnScreen,
  }
}

export interface PinControl {
  on: boolean
  // Busy, not disabled, so the pin keeps focus while its command records.
  busy: boolean
  onPress: () => void
}

// What a press of a pin sends: the value command where the value is not yet
// the owner's choice, then the pin; else the pin or the unpin alone.
export const pinControls =
  ({
    busy,
    choose,
    pinAfter,
    shown,
  }: {
    busy: boolean
    choose: (
      outcome: DecisionCommand['outcome'],
      selectedValue?: DecisionCommand['selectedValue'],
    ) => string
    pinAfter: (idempotencyKey: string) => void
    // The pin's status is said at the foot of the field from here on.
    shown: () => void
  }) =>
  (
    pin: { on: boolean; press: PinPress } | null,
    selectedValue?: DecisionCommand['selectedValue'],
  ): PinControl | undefined =>
    pin
      ? {
          on: pin.on,
          busy,
          onPress: () => {
            if (busy) return
            if (pin.press.kind === 'value_then_pin')
              pinAfter(choose(pin.press.outcome, selectedValue))
            else choose(pin.press.kind)
            shown()
          },
        }
      : undefined
