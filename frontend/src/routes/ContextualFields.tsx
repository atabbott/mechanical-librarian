// The selected work's contextual fields: a one-line summary of those with no
// change, waiting or unavailable, which opens to say why for each. A field
// with nothing to decide is pinned and unpinned from its row and stays in it
// (#1346). Moved out of the Library route under the route-size rule (#1903).
import { useEffect, useId, useRef, useState } from 'react'

import { StatusPill } from '../components/StatusPill'
import { decisionValue } from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import { valuePin } from '../fieldPin'
import { contextKind, fieldReason } from '../fieldReasons'
import { destinationLabel } from '../labels'
import type { useReviewDecisionController } from '../reviewDecisionController'
import { PinButton } from './FieldDecisionPin'

// What a press is doing or did: announced in the row, never printed under it.
const QUIET = new Set(['Pinning…', 'Unpinning…', 'Pinned.', 'Unpinned.'])

const heldPhrase = (count: number, kind: 'waiting' | 'unavailable') =>
  `${String(count)} ${count === 1 ? 'field' : 'fields'} ${kind === 'waiting' ? 'waiting' : 'unavailable'}`

export function ContextualFields({
  fields,
  controller,
  choose,
  notice,
}: {
  fields: readonly ReviewField[]
  controller: Pick<
    ReturnType<typeof useReviewDecisionController>,
    'recording' | 'checking' | 'reloadRequired' | 'statusFor' | 'alertFor' | 'messageFor'
  >
  choose: (field: ReviewField, outcome: 'pin' | 'unpin') => unknown
  // What the owner is told on the case that replaced the one a pin was pressed on.
  notice: { destination: string; text: string } | null
}) {
  const id = useId()
  const list = useRef<HTMLDivElement>(null)
  const told =
    notice && fields.some((field) => field.destination === notice.destination) ? notice : null
  // Open is the panel's own, so a refreshed case never closes it under a press.
  const [open, setOpen] = useState(told !== null)
  // The row whose pin was pressed this visit: what the press did is said there.
  const [pressed, setPressed] = useState<string | null>(null)
  const [opened, setOpened] = useState(told)
  if (told !== opened) {
    setOpened(told)
    if (told) setOpen(true)
  }
  useEffect(() => {
    if (!opened) return
    const frame = requestAnimationFrame(() =>
      // The row's pin, described by the notice; a row with no pin, the notice itself.
      list.current
        ?.querySelector<HTMLElement>('[data-told] .choice-pin, [data-told] [role="status"]')
        ?.focus({ preventScroll: true }),
    )
    return () => cancelAnimationFrame(frame)
  }, [opened])
  const contextualSummary = {
    unchanged: fields.filter((field) => contextKind(field) === 'unchanged'),
    waiting: fields.filter((field) => contextKind(field) === 'waiting').length,
    unavailable: fields.filter((field) => contextKind(field) === 'unavailable').length,
  }
  const pinnedCount = contextualSummary.unchanged.filter(
    (field) => field.pin?.state === 'in_force',
  ).length
  const busy = controller.recording || controller.checking || controller.reloadRequired
  return (
    <details className="automatic-resolution-details" open={open}>
      <summary
        onClick={(event) => {
          event.preventDefault()
          setOpen(!open)
        }}
      >
        <span>
          {contextualSummary.unchanged.length ? (
            <span>
              No change:{' '}
              {contextualSummary.unchanged
                .map((field) => destinationLabel(field.destination))
                .join(', ')}
              {/* No-break spaces: the dot and the count never part from the word. */}
              {pinnedCount ? ` ·\u00a0${String(pinnedCount)}\u00a0pinned` : null}
            </span>
          ) : null}
          {contextualSummary.waiting ? (
            <span>{heldPhrase(contextualSummary.waiting, 'waiting')}</span>
          ) : null}
          {contextualSummary.unavailable ? (
            <span>{heldPhrase(contextualSummary.unavailable, 'unavailable')}</span>
          ) : null}
        </span>
        <small>{open ? 'Hide' : 'Show why'}</small>
      </summary>
      <div className="automatic-resolution-list" ref={list}>
        {fields.map((field) => {
          const kind = contextKind(field)
          const { destination } = field
          // Only a row that reads unchanged carries a pin: a waiting or
          // unavailable row keeps its own word.
          const pin = kind === 'unchanged' ? valuePin(field, 'current') : null
          const pinned = kind === 'unchanged' && field.pin?.state === 'in_force'
          const here = pressed === destination
          const locked = controller.reloadRequired ? controller.messageFor(destination) : undefined
          const alert = here && (controller.alertFor(destination) || locked !== undefined)
          const said = here ? controller.statusFor(destination) || (locked ?? '') : ''
          const toldHere = told?.destination === destination
          const status = toldHere ? told.text : alert ? '' : said
          const nameId = `${id}-${destination}-name`
          const valueId = `${id}-${destination}-value`
          const noticeId = `${id}-${destination}-notice`
          return (
            <div
              className={`automatic-resolution-row is-${kind}`}
              key={destination}
              data-told={toldHere || undefined}
            >
              <div className="context-row-head">
                <strong>
                  <span id={nameId}>{destinationLabel(destination)}</span>
                  {pinned ? <StatusPill tone="accent">Pinned</StatusPill> : null}
                  {kind === 'unchanged' ? null : (
                    <StatusPill tone="notice">
                      {kind === 'waiting' ? 'Waiting' : 'Unavailable'}
                    </StatusPill>
                  )}
                </strong>
                {pin ? (
                  <PinButton
                    on={pin.on}
                    busy={busy}
                    describedBy={`${nameId} ${valueId}${toldHere ? ` ${noticeId}` : ''}`}
                    onPress={() => {
                      if (busy) return
                      setPressed(destination)
                      choose(field, pin.press.kind === 'unpin' ? 'unpin' : 'pin')
                    }}
                  />
                ) : null}
              </div>
              <small id={valueId}>{decisionValue(field)}</small>
              <small>
                {pinned
                  ? field.pin?.value.state === 'value'
                    ? 'You pinned this value.'
                    : 'You pinned this field empty.'
                  : kind === 'unchanged'
                    ? 'No source offered a different value.'
                    : fieldReason(field)}
              </small>
              {/* Each is mounted empty before it speaks, so what a press did is announced. */}
              {pin || toldHere ? (
                <p
                  role="status"
                  tabIndex={-1}
                  id={noticeId}
                  className={QUIET.has(status) ? 'sr-only' : undefined}
                >
                  {status}
                </p>
              ) : null}
              {here ? <p role="alert">{alert ? said : ''}</p> : null}
            </div>
          )
        })}
      </div>
    </details>
  )
}
