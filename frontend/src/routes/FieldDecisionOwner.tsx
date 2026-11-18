// A Library field's owner controls. Moved out of the Library route under the
// route-size rule (#1571).
import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent, RefObject, SyntheticEvent } from 'react'

import { displayValue, isClear } from '../api/http'
import type { DecisionCommand } from '../api/http'
import type { ReviewField } from '../fieldDecision'
import { clearedSentence, destinationLabel, destinationPhrase, listPhrase } from '../labels'
import { writtenRefusal } from '../reviewDecisionController'
import { StatusPill } from '../components/StatusPill'
import type { PinControl } from '../fieldPin'
import { PinButton } from './FieldDecisionPin'

// F59's owner controls (ADR-0085): enter a value, choose genres, enter names
// and withdraw a decision. Going back to an earlier value is choosing it
// again, from the same suggestions; emptying a clearable field clears it
// (ADR-0105). Each is offered only where the store would accept it now; the
// store still decides, and a refusal is said here, beside the control used.
export type OwnerOutcome = 'owner_entered' | 'withdraw'
const ownerText = (value: string) => value.normalize('NFC').replace(/\s+/gu, ' ').trim()
// One name per line; a blank line is not a name.
const namesOf = (text: string) => text.split('\n').map(ownerText).filter(Boolean)
// Names that differ only by case or composition are one name, as the store
// compares them (#1523). The store case-folds; this folds each character the
// same way: the round trip through capitals takes ß and ẞ to "ss", and a
// dotless ı stays itself, because case folding keeps it apart from i and I.
const foldCharacter = (character: string) =>
  character === 'ı' ? character : character.toUpperCase().toLowerCase().toUpperCase().toLowerCase()
const nameKey = (name: string) =>
  Array.from(name.normalize('NFC'), foldCharacter).join('').normalize('NFC')
const sameList = (left: readonly string[], right: unknown) =>
  Array.isArray(right) &&
  left.length === right.length &&
  left.every((item, index) => item === right[index])
const EMPTY_VALUE = { kind: 'empty', value: null } as const
// A clear has no value to quote; its own sentence says it (#1523).
const quotedValue = (value: { kind: string; value: unknown } | null | undefined) =>
  value && !isClear(value) ? `“${displayValue(value.value)}”` : 'nothing'
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
  pin,
}: {
  field: ReviewField
  disabled: boolean
  progress: { command: DecisionCommand; phase: 'recording' | 'confirmed' | 'failed' } | null
  status: string
  failed: boolean
  submit: (outcome: OwnerOutcome, value: DecisionCommand['selectedValue']) => string
  // The pin beside the value the owner entered, which has no card (#1346).
  pin?: PinControl | undefined
}) {
  const control = field.ownerControl ?? null
  const label = destinationLabel(field.destination)
  const [mode, setMode] = useState<'idle' | 'entry' | 'subset' | 'names'>('idle')
  const [draft, setDraft] = useState('')
  const [namesDraft, setNamesDraft] = useState('')
  const [chosen, setChosen] = useState<readonly string[]>([])
  const [problem, setProblem] = useState('')
  // The notice or refusal the owner has moved past by opening or closing a
  // form; it stays unsaid until the next command.
  const [dismissed, setDismissed] = useState<string | null>(null)
  const recordedId = useId()
  const inputId = useId()
  const countId = useId()
  const problemId = useId()
  const hintId = useId()
  const input = useRef<HTMLInputElement>(null)
  const namesInput = useRef<HTMLTextAreaElement>(null)
  const subsetGroup = useRef<HTMLFieldSetElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const recorded = useRef<HTMLParagraphElement>(null)
  const notice = useRef<HTMLParagraphElement>(null)
  const statusLine = useRef<HTMLParagraphElement>(null)
  const sent = useRef<{ key: string; outcome: OwnerOutcome } | null>(null)
  const section = useRef<HTMLElement>(null)
  // A withdrawal takes away the recorded value and its context, so the detail
  // column keeps its height until this field closes: the page does not move
  // under the outcome line on a phone (#1365). This component is keyed by work
  // and field, so another field, work or route releases the hold.
  const held = useRef<HTMLElement | null>(null)
  useEffect(
    () => () => {
      held.current?.style.removeProperty('min-height')
    },
    [],
  )
  const busy = progress?.phase === 'recording'
  const decision = field.humanDecision
  // Any standing decision is changed, not entered afresh (D5).
  const changing = decision !== null && decision.outcome !== 'withdraw'
  const refocus = useRef<RefObject<HTMLButtonElement | null> | null>(null)
  useEffect(() => {
    if (mode === 'entry') input.current?.focus()
    if (mode === 'names') namesInput.current?.focus()
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
  const { entry, subset, names, current } = control
  const writing = control.withdraw === 'in_progress'
  const noun = destinationPhrase(field.destination)
  const currentEmpty =
    current === null || current === '' || (Array.isArray(current) && current.length === 0)
  const recordedValue = decision?.selected_value
  const recordedClear = changing && isClear(recordedValue)
  // The owner's standing value, which a new one must differ from as well as
  // from the field's current value; each refusal says which it matched (#1523).
  const standing = changing && !recordedClear ? (recordedValue?.value ?? null) : null
  // An emptied form clears a clearable field that holds a value, unless the
  // owner's standing value is already a clear (ADR-0105).
  const clearing = (empty: boolean) => control.clear && empty && !currentEmpty && !recordedClear
  // An emptied form that clears nothing says why, or what to enter (#1523).
  const emptied = (remedy: string) =>
    recordedClear
      ? clearedSentence(field.destination, true)
      : changing && control.clear && currentEmpty && control.withdraw === 'available'
        ? 'This field is already empty. Use Withdraw to remove your value.'
        : remedy
  const blocked = disabled || busy
  const send = (outcome: OwnerOutcome, value: DecisionCommand['selectedValue']) => {
    // The ref is the guard a second press meets before React re-renders.
    if (blocked || sent.current !== null) return
    setProblem('')
    setDismissed(null)
    const column = section.current?.closest<HTMLElement>('.workspace-detail')
    if (outcome === 'withdraw' && column) {
      column.style.minHeight = `${String(column.getBoundingClientRect().height)}px`
      held.current = column
    }
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
  // What each form opens holding. A clearable field opens with its value, so
  // emptying it is the owner's act; names entry opens with the field's names.
  const recordedText = recordedValue?.value
  const entryOpening =
    changing && typeof recordedText === 'string'
      ? recordedText
      : !recordedClear && control.clear && typeof current === 'string'
        ? current
        : ''
  const subsetOpening: readonly string[] =
    changing && Array.isArray(recordedText)
      ? (recordedText as string[])
      : recordedClear
        ? []
        : (subset?.options.filter((option) => option.current).map((option) => option.value) ?? [])
  const namesOpening: readonly string[] =
    changing && Array.isArray(recordedText)
      ? (recordedText as string[])
      : !recordedClear && Array.isArray(current)
        ? current
        : []
  // A form that opens holding a value changes it (#1523).
  const triggerLabel = (opening: boolean, otherwise: string) =>
    changing || opening ? 'Change' : otherwise
  const openEntry = () => {
    setDraft(entryOpening)
    setProblem('')
    setDismissed(status)
    setMode('entry')
  }
  const openSubset = () => {
    setChosen(subsetOpening)
    setProblem('')
    setDismissed(status)
    setMode('subset')
  }
  const openNames = () => {
    setNamesDraft(namesOpening.join('\n'))
    setProblem('')
    setDismissed(status)
    setMode('names')
  }
  const recordEntry = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!entry || blocked) return
    const value = ownerText(draft)
    setDraft(value)
    if (!value) {
      if (clearing(true)) send('owner_entered', EMPTY_VALUE)
      else
        setProblem(
          emptied(field.destination === 'title' ? 'Enter a title.' : 'Enter a value first.'),
        )
    } else if (Array.from(value).length > entry.maxCharacters)
      setProblem(`Use up to ${String(entry.maxCharacters)} characters.`)
    else if (value === current) setProblem('That is already the current value.')
    else if (value === standing) setProblem('You already entered this value.')
    else send('owner_entered', { kind: 'text', value })
  }
  const recordNames = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!names || blocked) return
    const value = namesOf(namesDraft)
    setNamesDraft(value.join('\n'))
    if (!value.length) {
      if (clearing(true)) send('owner_entered', EMPTY_VALUE)
      else
        setProblem(
          emptied(
            field.destination === 'authors'
              ? 'Enter at least one author.'
              : 'Enter at least one name.',
          ),
        )
    } else if (value.length > names.maxItems)
      setProblem(`Use up to ${String(names.maxItems)} names.`)
    else if (new Set(value.map(nameKey)).size !== value.length) setProblem('Enter each name once.')
    else if (value.some((name) => Array.from(name).length > names.maxCharacters))
      setProblem(`Use up to ${String(names.maxCharacters)} characters for each name.`)
    else if (sameList(value, current)) setProblem('Those are already the current names.')
    else if (sameList(value, standing)) setProblem('You already entered these names.')
    else send('owner_entered', { kind: 'list', value })
  }
  const recordSubset = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!subset || blocked) return
    // Raw values in the store's union order, never display text.
    const value = subset.options
      .map((option) => option.value)
      .filter((option) => chosen.includes(option))
    if (!value.length) {
      if (clearing(true)) send('owner_entered', EMPTY_VALUE)
      else setProblem(emptied('Choose at least one genre.'))
    } else if (value.length > subset.maxItems)
      setProblem(`Choose up to ${String(subset.maxItems)} genres.`)
    else if (sameList(value, current)) setProblem('Those are already the current genres.')
    else if (sameList(value, standing)) setProblem('You already chose these genres.')
    else send('owner_entered', { kind: 'list', value })
  }
  // The recorded line names what was recorded, as nothing else on the field
  // shows it once the form is closed.
  const recordedList = Array.isArray(recordedValue?.value)
    ? (recordedValue.value as unknown[]).map(displayValue)
    : null
  const recordedCopy =
    decision?.outcome !== 'owner_entered'
      ? null
      : isClear(recordedValue)
        ? clearedSentence(field.destination)
        : recordedList === null
          ? `You entered ${quotedValue(recordedValue)}.`
          : `${field.destination === 'genres' ? 'You chose' : 'You entered'} ${listPhrase(recordedList)}.`
  // The record button of a form, which clears the field once it is emptied.
  const submitLabel = (empty: boolean, record: string) =>
    clearing(empty) ? (busy ? 'Clearing…' : `Clear ${noun}`) : busy ? 'Recording…' : record
  const actions = (empty: boolean, record: string) => (
    <div className="owner-actions">
      <button type="submit" aria-disabled={blocked}>
        {submitLabel(empty, record)}
      </button>
      <button type="button" onClick={() => close(trigger)}>
        Cancel
      </button>
    </div>
  )
  const characters = Array.from(draft).length
  const namesCount = namesOf(namesDraft).length
  // A refusal is said in one place: beside the input while a form is open,
  // and at the end of the section otherwise. Each place is mounted empty.
  const shown = status === dismissed ? '' : status
  const refusal = problem || (failed ? shown : '')
  const refusalNotice = (
    <p ref={notice} id={problemId} tabIndex={-1} className="owner-notice" role="alert">
      {refusal}
    </p>
  )
  const recordedLine = recordedCopy ? (
    <p ref={recorded} id={recordedId} tabIndex={-1} className="owner-recorded">
      {recordedCopy}
    </p>
  ) : null
  return (
    <section ref={section} className="owner-controls" aria-label={`Your value for ${label}`}>
      {recordedCopy && pin ? (
        <div className="owner-recorded-pin">
          {recordedLine}
          <PinButton {...pin} describedBy={recordedId} />
        </div>
      ) : (
        recordedLine
      )}
      {writing ? <p className="owner-note">{withdrawCopy.in_progress}</p> : null}
      {entry && mode !== 'entry' && !writing ? (
        <button
          ref={trigger}
          type="button"
          className="owner-trigger"
          data-review-key="e"
          aria-disabled={blocked}
          onClick={() => {
            if (!blocked) openEntry()
          }}
        >
          {triggerLabel(entryOpening !== '', 'Enter a value')}
        </button>
      ) : null}
      {names && mode !== 'names' && !writing ? (
        <button
          ref={trigger}
          type="button"
          className="owner-trigger"
          data-review-key="e"
          aria-disabled={blocked}
          onClick={() => {
            if (!blocked) openNames()
          }}
        >
          {triggerLabel(namesOpening.length > 0, 'Enter names')}
        </button>
      ) : null}
      {subset && mode !== 'subset' && !writing ? (
        <button
          ref={trigger}
          type="button"
          className="owner-trigger"
          data-review-key="e"
          aria-disabled={blocked}
          onClick={() => {
            if (!blocked) openSubset()
          }}
        >
          {triggerLabel(subsetOpening.length > 0, 'Choose genres')}
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
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => setDraft((value) => ownerText(value))}
          />
          {/* Said only once the value is too long (#1523). */}
          <small id={countId} className="owner-count" aria-live="polite">
            {characters > entry.maxCharacters
              ? `${String(characters)} of ${String(entry.maxCharacters)} characters`
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
                    input.current?.focus()
                  }}
                >
                  {suggestion.rule === 'previous'
                    ? 'Use previous value '
                    : suggestion.rule === 'pinned'
                      ? 'Use pinned value '
                      : 'Use '}
                  {/* Library text may break anywhere; the copy around it never does. */}
                  <span className="owner-suggestion-value">“{suggestion.value}”</span>
                </button>
              ))}
            </div>
          ) : null}
          {actions(!ownerText(draft), 'Record this value')}
        </form>
      ) : null}
      {names && mode === 'names' ? (
        <form className="owner-entry" onSubmit={recordNames} onKeyDown={escape} noValidate>
          <label htmlFor={inputId}>{`Your value for ${label}`}</label>
          <small id={hintId} className="owner-hint">
            One name per line
          </small>
          <textarea
            ref={namesInput}
            id={inputId}
            rows={Math.min(8, Math.max(3, namesDraft.split('\n').length))}
            value={namesDraft}
            autoComplete="off"
            aria-invalid={refusal ? true : undefined}
            aria-describedby={`${hintId} ${countId} ${problemId}`}
            onChange={(event) => setNamesDraft(event.target.value)}
          />
          <small id={countId} className="owner-count" aria-live="polite">
            {namesCount > names.maxItems
              ? `${String(namesCount)} of ${String(names.maxItems)} names`
              : ''}
          </small>
          {refusalNotice}
          {names.previous ? (
            <div className="owner-suggestions">
              <button
                type="button"
                onClick={() => {
                  setNamesDraft((names.previous ?? []).join('\n'))
                  namesInput.current?.focus()
                }}
              >
                {'Use previous names '}
                <span className="owner-suggestion-value">“{displayValue(names.previous)}”</span>
              </button>
            </div>
          ) : null}
          {actions(namesCount === 0, 'Record these names')}
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
          {actions(chosen.length === 0, 'Record these genres')}
        </form>
      ) : null}
      {control.withdraw === 'available' ? (
        <button
          type="button"
          className="owner-trigger"
          data-review-key="u"
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
