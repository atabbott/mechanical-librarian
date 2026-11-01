// A Library field's owner controls. Moved out of the Library route under the
// route-size rule (#1571).
import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent, RefObject, SyntheticEvent } from 'react'

import { displayValue } from '../api/http'
import type { DecisionCommand } from '../api/http'
import type { ReviewField } from '../fieldDecision'
import { destinationLabel } from '../labels'
import { writtenRefusal } from '../reviewDecisionController'
import { StatusPill } from '../components/StatusPill'

// F59's owner controls (ADR-0085): enter a value, choose genres and withdraw a
// decision. Going back to an earlier value is choosing it again, from the same
// suggestions. Each is offered only where the store would accept it now; the
// store still decides, and a refusal is said here, beside the control used.
export type OwnerOutcome = 'owner_entered' | 'withdraw'
const ownerText = (value: string) => value.normalize('NFC').replace(/\s+/gu, ' ').trim()
const quotedValue = (value: { value: unknown } | null | undefined) =>
  value ? `“${displayValue(value.value)}”` : 'nothing'
const withdrawCopy = {
  automatic_hold:
    'This decision is holding automatic handling, so it can’t be withdrawn. Use Resume automatic handling to release it.',
  in_progress:
    'This decision is being written to your library. You can change it once the write finishes.',
  written: writtenRefusal,
} as const

export function OwnerControls({
  field,
  disabled,
  progress,
  status,
  failed,
  submit,
}: {
  field: ReviewField
  disabled: boolean
  progress: { command: DecisionCommand; phase: 'recording' | 'confirmed' | 'failed' } | null
  status: string
  failed: boolean
  submit: (outcome: OwnerOutcome, value: DecisionCommand['selectedValue']) => string
}) {
  const control = field.ownerControl ?? null
  const label = destinationLabel(field.destination)
  const [mode, setMode] = useState<'idle' | 'entry' | 'subset'>('idle')
  const [draft, setDraft] = useState('')
  const [typed, setTyped] = useState(false)
  const [chosen, setChosen] = useState<readonly string[]>([])
  const [problem, setProblem] = useState('')
  // The notice or refusal the owner has moved past by opening or closing a
  // form; it stays unsaid until the next command.
  const [dismissed, setDismissed] = useState<string | null>(null)
  const inputId = useId()
  const countId = useId()
  const problemId = useId()
  const input = useRef<HTMLInputElement>(null)
  const subsetGroup = useRef<HTMLFieldSetElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const recorded = useRef<HTMLParagraphElement>(null)
  const notice = useRef<HTMLParagraphElement>(null)
  const statusLine = useRef<HTMLParagraphElement>(null)
  const sent = useRef<{ key: string; outcome: OwnerOutcome } | null>(null)
  const busy = progress?.phase === 'recording'
  const decision = field.humanDecision
  // Any standing decision is changed, not entered afresh (D5).
  const changing = decision !== null && decision.outcome !== 'withdraw'
  const refocus = useRef<RefObject<HTMLButtonElement | null> | null>(null)
  useEffect(() => {
    if (mode === 'entry') input.current?.focus()
    if (mode === 'subset') subsetGroup.current?.querySelector('input')?.focus()
    if (mode === 'idle') refocus.current?.current?.focus({ preventScroll: true })
    refocus.current = null
  }, [mode])
  // A settled command leaves focus on something named: the recorded value,
  // the withdrawal's own outcome line where its controls were (#1365), or,
  // when a refusal removed the control that was used, the refusal itself.
  useEffect(() => {
    const command = sent.current
    if (!command) return
    const own = progress?.command.idempotencyKey === command.key ? progress : null
    if (own?.phase === 'recording') return
    sent.current = null
    if (own?.phase !== 'confirmed') {
      requestAnimationFrame(() => {
        if (!document.activeElement || document.activeElement === document.body)
          notice.current?.focus({ preventScroll: true })
      })
      return
    }
    setMode('idle')
    requestAnimationFrame(() => {
      ;(command.outcome === 'withdraw'
        ? statusLine.current
        : (recorded.current ?? notice.current)
      )?.focus({ preventScroll: true })
    })
  }, [progress])
  if (!control) return null
  const { entry, subset } = control
  const writing = control.withdraw === 'in_progress'
  const blocked = disabled || busy
  const send = (outcome: OwnerOutcome, value: DecisionCommand['selectedValue']) => {
    // The ref is the guard a second press meets before React re-renders.
    if (blocked || sent.current !== null) return
    setProblem('')
    setDismissed(null)
    sent.current = { key: submit(outcome, value), outcome }
  }
  const close = (target: RefObject<HTMLButtonElement | null>) => {
    refocus.current = target
    setMode('idle')
    setProblem('')
    setDismissed(status)
  }
  const escape = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return
    event.preventDefault()
    close(trigger)
  }
  const openEntry = () => {
    const recordedText = decision?.selected_value?.value
    setDraft(changing && typeof recordedText === 'string' ? recordedText : '')
    setTyped(false)
    setProblem('')
    setDismissed(status)
    setMode('entry')
  }
  const openSubset = () => {
    const recordedList = decision?.selected_value?.value
    setChosen(
      changing && Array.isArray(recordedList)
        ? (recordedList as string[])
        : (subset?.options.filter((option) => option.current).map((option) => option.value) ?? []),
    )
    setProblem('')
    setDismissed(status)
    setMode('subset')
  }
  const recordEntry = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!entry || blocked) return
    const value = ownerText(draft)
    setDraft(value)
    if (!value) setProblem('Enter a value first.')
    else if (Array.from(value).length > entry.maxCharacters)
      setProblem(`Use up to ${String(entry.maxCharacters)} characters.`)
    else if (value === field.currentRaw) setProblem('That is already the current value.')
    else send('owner_entered', { kind: 'text', value })
  }
  const recordSubset = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!subset || blocked) return
    // Raw values in the store's union order, never display text.
    const value = subset.options
      .map((option) => option.value)
      .filter((option) => chosen.includes(option))
    const current = Array.isArray(field.currentRaw) ? (field.currentRaw as unknown[]) : []
    if (!value.length) setProblem('Choose at least one genre.')
    else if (value.length > subset.maxItems)
      setProblem(`Choose up to ${String(subset.maxItems)} genres.`)
    else if (value.length === current.length && value.every((item, i) => item === current[i]))
      setProblem('Those are already the current genres.')
    else send('owner_entered', { kind: 'list', value })
  }
  const recordedCopy =
    decision?.outcome === 'owner_entered'
      ? Array.isArray(decision.selected_value?.value)
        ? `You chose ${String(decision.selected_value.value.length)} ${decision.selected_value.value.length === 1 ? 'genre' : 'genres'}.`
        : `You entered ${quotedValue(decision.selected_value)}.`
      : null
  // A refusal is said in one place: beside the input while a form is open,
  // and at the end of the section otherwise. Each place is mounted empty.
  const shown = status === dismissed ? '' : status
  const refusal = problem || (failed ? shown : '')
  const refusalNotice = (
    <p ref={notice} id={problemId} tabIndex={-1} className="owner-notice" role="alert">
      {refusal}
    </p>
  )
  return (
    <section className="owner-controls" aria-label={`Your value for ${label}`}>
      {recordedCopy ? (
        <p ref={recorded} tabIndex={-1} className="owner-recorded">
          {recordedCopy}
        </p>
      ) : null}
      {writing ? <p className="owner-note">{withdrawCopy.in_progress}</p> : null}
      {entry && mode !== 'entry' && !writing ? (
        <button ref={trigger} type="button" className="owner-trigger" onClick={openEntry}>
          {changing ? 'Change' : 'Enter a value'}
        </button>
      ) : null}
      {subset && mode !== 'subset' && !writing ? (
        <button ref={trigger} type="button" className="owner-trigger" onClick={openSubset}>
          {changing ? 'Change' : 'Choose genres'}
        </button>
      ) : null}
      {entry && mode === 'entry' ? (
        <form className="owner-entry" onSubmit={recordEntry} onKeyDown={escape} noValidate>
          <label htmlFor={inputId}>{`Your value for ${label}`}</label>
          <input
            ref={input}
            id={inputId}
            type="text"
            value={draft}
            autoComplete="off"
            aria-invalid={refusal ? true : undefined}
            aria-describedby={`${countId} ${problemId}`}
            onChange={(event) => {
              setDraft(event.target.value)
              setTyped(true)
            }}
            onBlur={() => setDraft((value) => ownerText(value))}
          />
          <small id={countId} className="owner-count" aria-live="polite">
            {typed
              ? `${String(Array.from(draft).length)} of ${String(entry.maxCharacters)} characters`
              : ''}
          </small>
          {refusalNotice}
          {entry.suggestions.length ? (
            <div className="owner-suggestions">
              {entry.suggestions.map((suggestion) => (
                <button
                  key={suggestion.rule}
                  type="button"
                  onClick={() => {
                    setDraft(suggestion.value)
                    setTyped(true)
                    input.current?.focus()
                  }}
                >
                  {suggestion.rule === 'previous' ? 'Use previous value ' : 'Use '}
                  {/* Library text may break anywhere; the copy around it never does. */}
                  <span className="owner-suggestion-value">“{suggestion.value}”</span>
                </button>
              ))}
            </div>
          ) : null}
          <div className="owner-actions">
            <button type="submit" aria-disabled={blocked}>
              {busy ? 'Recording…' : 'Record this value'}
            </button>
            <button type="button" onClick={() => close(trigger)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {subset && mode === 'subset' ? (
        <form className="owner-entry" onSubmit={recordSubset} onKeyDown={escape} noValidate>
          <fieldset ref={subsetGroup} className="owner-subset" aria-describedby={problemId}>
            <legend>Choose genres</legend>
            {subset.options.map((option) => (
              <label key={option.value} className="owner-option">
                <input
                  type="checkbox"
                  checked={chosen.includes(option.value)}
                  onChange={(event) => {
                    const checked = event.target.checked
                    setChosen((values) =>
                      checked
                        ? [...values, option.value]
                        : values.filter((value) => value !== option.value),
                    )
                  }}
                />
                <span>{option.value}</span>
                {/* One unit, so a narrow row wraps its tags together. */}
                <span className="owner-tags">
                  {option.current ? <StatusPill>Current</StatusPill> : null}
                  {option.proposed ? <StatusPill>Proposed</StatusPill> : null}
                  {option.previous ? <StatusPill>Previous</StatusPill> : null}
                </span>
              </label>
            ))}
          </fieldset>
          {refusalNotice}
          <div className="owner-actions">
            <button type="submit" aria-disabled={blocked}>
              {busy ? 'Recording…' : 'Record these genres'}
            </button>
            <button type="button" onClick={() => close(trigger)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {control.withdraw === 'available' ? (
        <button
          type="button"
          className="owner-trigger"
          aria-disabled={blocked}
          onClick={() => send('withdraw', null)}
        >
          {busy && progress.command.outcome === 'withdraw' ? 'Withdrawing…' : 'Withdraw'}
        </button>
      ) : control.withdraw &&
        control.withdraw !== 'in_progress' &&
        // The refusal already says it in the same words.
        !(control.withdraw === 'written' && refusal === writtenRefusal) ? (
        <p className="owner-note">{withdrawCopy[control.withdraw]}</p>
      ) : null}
      {mode === 'idle' ? refusalNotice : null}
      <p ref={statusLine} tabIndex={-1} className="owner-status" role="status">
        {refusal || failed ? '' : shown}
      </p>
    </section>
  )
}
