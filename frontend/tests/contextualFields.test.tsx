import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { CaseDetailView } from '../src/api/http'
import { ContextualFields } from '../src/routes/ContextualFields'
import { detail } from './fixtures'

// The pin in the No change panel (#1346): a field with nothing to decide is
// pinned from its row and stays in it.
type Field = CaseDetailView['fields'][number]
type Props = ComponentProps<typeof ContextualFields>
const title = detail.fields[0]
if (!title) throw new Error('workspace fixture requires a title field')
const row = (
  destination: Field['destination'],
  value: string,
  over: Partial<Field> = {},
): Field => ({
  ...title,
  classification: 'unsupported',
  classificationCause: 'nothing_to_decide',
  classificationReason: 'Nothing to decide for this field.',
  destination,
  currentValue: value,
  currentRaw: value,
  decisionActions: [],
  editable: false,
  inventoryRole: 'contextual',
  needsAttention: false,
  navigationRole: 'inspect',
  proposalId: `proposal-${destination}`,
  proposedValues: [],
  ownerControl: null,
  pin: null,
  pinControl: { pin: 'available', unpin: false },
  ...over,
})
const pinned = (field: Field, empty = false): Field => ({
  ...field,
  classification: 'human_recorded',
  classificationCause: 'pinned',
  classificationReason:
    'You pinned this value. Mechanical Librarian will not ask about it or change it unless it changes in Audiobookshelf.',
  pin: {
    state: 'in_force',
    value: empty
      ? { destination: field.destination, state: 'missing', value: null, formatted: null }
      : ({
          destination: field.destination,
          state: 'value',
          value: field.currentValue,
          formatted: null,
        } as never),
  },
  pinControl: { pin: 'available', unpin: true },
})
const publisher = row('publisher', 'Example House')
const language = row('language', 'English')
const quiet: Props['controller'] = {
  recording: false,
  checking: false,
  reloadRequired: false,
  statusFor: () => '',
  alertFor: () => false,
  messageFor: () => undefined,
}
const show = (fields: Field[], over: Partial<Props> = {}) => {
  const choose = vi.fn<Props['choose']>()
  const props = { fields, controller: quiet, choose, notice: null, ...over }
  const view = render(<ContextualFields {...props} />)
  const details = view.container.querySelector('details')
  if (!details) throw new Error('the panel is missing')
  return {
    choose,
    details,
    again: (next: Partial<Props>) => view.rerender(<ContextualFields {...props} {...next} />),
  }
}
const rowOf = (name: string) => {
  const found = screen
    .getByText(name, { selector: 'strong > span' })
    .closest('.automatic-resolution-row')
  if (!(found instanceof HTMLElement)) throw new Error(`no row for ${name}`)
  return found
}
const openPanel = async (details: HTMLElement) => {
  const summary = details.querySelector('summary')
  if (!summary) throw new Error('the panel has no summary')
  await userEvent.click(summary)
}

describe('the pin on a field with nothing to decide', () => {
  it('stays closed as before and opens to an outline pin on each unchanged row', async () => {
    const { details } = show([publisher, language])
    expect(details).not.toHaveAttribute('open')
    expect(details.querySelector('summary')).toHaveTextContent(
      /^No change: Publisher, LanguageShow why$/u,
    )
    await openPanel(details)
    expect(details).toHaveAttribute('open')
    // Open, the control says what a press now does; closed, it offers the reasons again.
    expect(details.querySelector('summary')).toHaveTextContent(
      /^No change: Publisher, LanguageHide$/u,
    )
    await openPanel(details)
    expect(details).not.toHaveAttribute('open')
    expect(details.querySelector('summary')).toHaveTextContent(
      /^No change: Publisher, LanguageShow why$/u,
    )
    await openPanel(details)
    for (const [name, value] of [
      ['Publisher', 'Example House'],
      ['Language', 'English'],
    ] as const) {
      const pin = within(rowOf(name)).getByRole('button', { name: 'Pin this value' })
      expect(pin).toHaveAccessibleDescription(`${name} ${value}`)
      expect(pin).not.toHaveClass('is-on')
      expect(pin).not.toHaveAttribute('aria-pressed')
      expect(pin.closest('.context-row-head')).not.toBeNull()
      expect(rowOf(name)).toHaveTextContent('No source offered a different value.')
      expect(rowOf(name)).not.toHaveTextContent('Pinned')
      expect(within(rowOf(name)).getByRole('status')).toBeEmptyDOMElement()
      expect(within(rowOf(name)).queryByRole('alert')).toBeNull()
    }
  })

  it('shows a contextual description as one line of visible text', async () => {
    const { details } = show([row('description', '<p>One <b>bold</b></p><p>Two</p>')])
    await openPanel(details)
    const pin = within(rowOf('Description')).getByRole('button', { name: 'Pin this value' })
    expect(pin).toHaveAccessibleDescription('Description One bold Two')
    expect(rowOf('Description').querySelector('small strong, small p')).toBeNull()
    expect(rowOf('Description')).not.toHaveTextContent('<b>')
  })

  it('shows no pin on a cover, a row the server offers none, a change waiting, or a held row', () => {
    show([
      row('cover', 'Cover on file', { pinControl: null }),
      row('publisher', 'Example House', { pinControl: null }),
      row('language', 'English', { pinControl: { pin: 'change_waiting', unpin: false } }),
      // The server may offer one; a Waiting or Unavailable row keeps its own word.
      row('subtitle', 'A Novel', { classificationCause: 'external_field_failure' }),
      row('asin', 'B000000000', { classificationCause: 'unsupported' }),
      // A changed pin on a suspended field waits in its row with no pin.
      row('isbn', '9780000000002', {
        classificationCause: 'external_field_failure',
        pin: {
          state: 'changed',
          value: { destination: 'isbn', state: 'missing', value: null, formatted: null },
        },
        pinControl: { pin: 'available', unpin: true },
      }),
    ])
    expect(screen.queryByRole('button', { hidden: true })).toBeNull()
    expect(screen.queryByRole('status', { hidden: true })).toBeNull()
    expect(rowOf('Language')).not.toHaveTextContent('A change to this field')
    expect(rowOf('Subtitle')).toHaveTextContent('Waiting')
    expect(rowOf('ISBN')).toHaveTextContent('Waiting')
    expect(rowOf('ISBN')).not.toHaveTextContent('Pinned')
    expect(rowOf('ASIN')).toHaveTextContent('Unavailable')
  })

  it('sends one pin for a press and ignores a press while a command records', async () => {
    const { choose, details, again } = show([publisher, language])
    await openPanel(details)
    const pin = within(rowOf('Language')).getByRole('button', { name: 'Pin this value' })
    await userEvent.click(pin)
    expect(choose).toHaveBeenCalledTimes(1)
    expect(choose).toHaveBeenCalledWith(language, 'pin')
    again({ controller: { ...quiet, recording: true, statusFor: () => 'Pinning…' } })
    // Busy, not disabled: the pin keeps focus and takes no second press.
    expect(pin).toHaveAttribute('aria-disabled', 'true')
    expect(pin).toBeEnabled()
    expect(pin).toHaveFocus()
    await userEvent.click(pin)
    await userEvent.click(within(rowOf('Publisher')).getByRole('button'))
    expect(choose).toHaveBeenCalledTimes(1)
    // What the press is doing is announced in the pressed row alone, and adds no line to it.
    expect(within(rowOf('Language')).getByRole('status')).toHaveTextContent('Pinning…')
    expect(within(rowOf('Language')).getByRole('status')).toHaveClass('sr-only')
    again({ controller: { ...quiet, recording: true, statusFor: () => 'Unpinning…' } })
    expect(within(rowOf('Language')).getByRole('status')).toHaveTextContent('Unpinning…')
    expect(within(rowOf('Language')).getByRole('status')).toHaveClass('sr-only')
    expect(within(rowOf('Publisher')).getByRole('status')).toBeEmptyDOMElement()
    expect(within(rowOf('Publisher')).queryByRole('alert')).toBeNull()
  })

  it('keeps a pinned row in the panel, says Pinned and counts it', async () => {
    const { choose, details, again } = show([publisher, language])
    await openPanel(details)
    await userEvent.click(within(rowOf('Publisher')).getByRole('button'))
    again({
      fields: [pinned(publisher), language],
      controller: { ...quiet, statusFor: () => 'Pinned.' },
    })
    // The refreshed case leaves the panel open and the same button focused.
    expect(details).toHaveAttribute('open')
    const unpin = within(rowOf('Publisher')).getByRole('button', { name: 'Unpin' })
    expect(unpin).toHaveFocus()
    expect(unpin).toHaveClass('is-on')
    expect(rowOf('Publisher')).toHaveClass('is-unchanged')
    expect(within(rowOf('Publisher')).getByText('Pinned')).toHaveClass('status-pill', 'is-accent')
    expect(rowOf('Publisher')).toHaveTextContent('You pinned this value.')
    expect(rowOf('Publisher')).not.toHaveTextContent('Mechanical Librarian will not ask')
    expect(rowOf('Publisher')).not.toHaveTextContent('No source offered a different value.')
    expect(rowOf('Publisher')).not.toHaveTextContent('Unavailable')
    // The word is announced and not printed a second time under the row.
    const status = within(rowOf('Publisher')).getByRole('status')
    expect(status).toHaveTextContent('Pinned.')
    expect(status).toHaveClass('sr-only')
    expect(within(rowOf('Publisher')).getByRole('alert')).toBeEmptyDOMElement()
    expect(within(rowOf('Language')).getByRole('status')).toBeEmptyDOMElement()
    const summary = details.querySelector('summary')
    expect(summary).toHaveTextContent(/^No change: Publisher, Language · 1 pinnedHide$/u)
    // The dot and the count never part from the word at a line end.
    expect(summary?.textContent).toContain('Language ·\u00a01\u00a0pinned')
    again({ fields: [pinned(publisher), pinned(language, true)] })
    expect(summary).toHaveTextContent(/^No change: Publisher, Language · 2 pinnedHide$/u)
    expect(rowOf('Language')).toHaveTextContent('You pinned this field empty.')
    await userEvent.click(within(rowOf('Publisher')).getByRole('button', { name: 'Unpin' }))
    expect(choose).toHaveBeenLastCalledWith(pinned(publisher), 'unpin')
    again({ fields: [publisher, language], controller: { ...quiet, statusFor: () => 'Unpinned.' } })
    expect(summary).toHaveTextContent(/^No change: Publisher, LanguageHide$/u)
    expect(within(rowOf('Publisher')).getByRole('status')).toHaveTextContent('Unpinned.')
    expect(within(rowOf('Publisher')).getByRole('status')).toHaveClass('sr-only')
  })

  it('says a failed pin and a reload lock in the pressed row’s alert', async () => {
    const { details, again } = show([publisher, language])
    await openPanel(details)
    await userEvent.click(within(rowOf('Publisher')).getByRole('button'))
    const failure = 'The pin was not recorded. Press the pin again.'
    again({ controller: { ...quiet, statusFor: () => failure, alertFor: () => true } })
    expect(within(rowOf('Publisher')).getByRole('alert')).toHaveTextContent(failure)
    expect(within(rowOf('Publisher')).getByRole('alert')).not.toHaveClass('sr-only')
    expect(within(rowOf('Publisher')).getByRole('status')).toBeEmptyDOMElement()
    expect(rowOf('Language')).not.toHaveTextContent(failure)
    const lock = 'This work could not be reloaded after your choice.'
    again({
      controller: {
        ...quiet,
        reloadRequired: true,
        messageFor: (destination) => (destination === 'publisher' ? lock : undefined),
      },
    })
    expect(within(rowOf('Publisher')).getByRole('alert')).toHaveTextContent(lock)
    expect(within(rowOf('Publisher')).getByRole('button')).toHaveAttribute('aria-disabled', 'true')
  })

  it('opens on a notice for one of its rows and puts focus on that row’s pin, described by it', async () => {
    const text = 'Not recorded: the work changed. Review it again.'
    const { details, again } = show([publisher, language])
    expect(details).not.toHaveAttribute('open')
    // A notice for a field with a tab of its own is not the panel's.
    again({ notice: { destination: 'title', text } })
    expect(details).not.toHaveAttribute('open')
    again({ notice: { destination: 'language', text } })
    expect(details).toHaveAttribute('open')
    const status = within(rowOf('Language')).getByRole('status')
    expect(status).toHaveTextContent(text)
    expect(status).not.toHaveClass('sr-only')
    const pin = within(rowOf('Language')).getByRole('button', { name: 'Pin this value' })
    await waitFor(() => expect(pin).toHaveFocus())
    expect(pin).toHaveAccessibleDescription(`Language English ${text}`)
    expect(
      within(rowOf('Publisher')).getByRole('button', { name: 'Pin this value' }),
    ).toHaveAccessibleDescription('Publisher Example House')
    expect(within(rowOf('Publisher')).getByRole('status')).toBeEmptyDOMElement()
    // The owner can still close the panel.
    await act(() => openPanel(details))
    expect(details).not.toHaveAttribute('open')
  })

  it('puts focus on the notice itself in a row that has no pin', async () => {
    const text = 'Not recorded: the work changed. Review it again.'
    const { details } = show(
      [publisher, row('language', 'English', { classificationCause: 'external_field_failure' })],
      { notice: { destination: 'language', text } },
    )
    expect(details).toHaveAttribute('open')
    expect(within(rowOf('Language')).queryByRole('button')).toBeNull()
    const status = within(rowOf('Language')).getByRole('status')
    expect(status).toHaveTextContent(text)
    await waitFor(() => expect(status).toHaveFocus())
  })
})
