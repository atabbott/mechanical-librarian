// The pin on a value (ADR-0085, Amendment 2): the icon button each value
// carries, and the foot of the field that says what a pin did.
import { useEffect, useRef } from 'react'

import type { ReviewField } from '../fieldDecision'
import { footAlert, pinFoot } from '../fieldPin'
import type { PinControl } from '../fieldPin'
import { destinationLabel } from '../labels'

export function PinButton({
  on,
  busy,
  onPress,
  describedBy,
}: PinControl & {
  // The value this pin is for, so several pins on a field can be told apart.
  describedBy?: string
}) {
  // The name says what a press does and the word Pinned beside the value says
  // the state, so the button carries no pressed state of its own.
  const name = on ? 'Unpin' : 'Pin this value'
  return (
    <button
      type="button"
      className={`choice-pin${on ? ' is-on' : ''}`}
      aria-label={name}
      title={name}
      aria-describedby={describedBy}
      aria-disabled={busy || undefined}
      onClick={onPress}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path className="head" d="M9 4h6l-1 6 3 3v2H7v-2l3-3-1-6z" />
        <path d="M12 15v6" />
      </svg>
    </button>
  )
}

export function FieldPinFoot({
  field,
  pinOnScreen,
  pressed,
  status,
  alert,
  busy,
  onUnpin,
}: {
  field: ReviewField
  // A card or value on screen carries the pin, so Unpin is pressed there.
  pinOnScreen: boolean
  // A pin of this field was pressed: a failure is said here from then on.
  pressed: boolean
  status: string
  alert: boolean
  busy: boolean
  onUnpin: () => void
}) {
  const { sentence, unpin, quiet } = pinFoot(field, { pinOnScreen })
  // The sentence already says the value is pinned: the word is still
  // announced, and not printed a second time under it.
  const said = alert ? '' : status
  const repeats = said === 'Pinned.' && sentence !== null && !quiet
  const section = useRef<HTMLElement>(null)
  // A settled press that took its own control away leaves focus on what the
  // foot says, never on nothing.
  const wasBusy = useRef(busy)
  useEffect(() => {
    const settled = wasBusy.current && !busy
    wasBusy.current = busy
    if (!settled) return
    const frame = requestAnimationFrame(() => {
      if (document.activeElement && document.activeElement !== document.body) return
      section.current
        ?.querySelector<HTMLElement>('.pin-note, .pin-why, [role="status"]')
        ?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [busy])
  return (
    <section
      ref={section}
      className={`pin-foot${(sentence && !quiet) || unpin ? ' has-note' : ''}`}
      aria-label={`Pin on ${destinationLabel(field.destination)}`}
    >
      {sentence ? (
        <p className={quiet ? 'pin-why' : 'pin-note'} tabIndex={-1}>
          {sentence}
        </p>
      ) : null}
      {unpin ? (
        <button
          type="button"
          className="owner-trigger"
          aria-disabled={busy || undefined}
          onClick={() => {
            if (!busy) onUnpin()
          }}
        >
          Unpin
        </button>
      ) : null}
      {/* Each is mounted empty before it speaks, so what a press did is announced. */}
      <p role="status" tabIndex={-1} className={repeats ? 'sr-only' : undefined}>
        {said}
      </p>
      {pressed ? <p role="alert">{alert ? footAlert(status, unpin) : ''}</p> : null}
    </section>
  )
}
