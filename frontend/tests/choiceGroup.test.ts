import { fireEvent, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { choiceRadios, moveChoiceFocus } from '../src/choiceGroup'

const states = (...choices: [checked: boolean, disabled: boolean][]) =>
  choiceRadios(choices, ([checked, disabled]) => ({ checked, disabled })).map(
    (choice) => choice.radio,
  )

describe('choice radio group', () => {
  it('puts the one Tab stop on the recorded choice, else the first enabled one', () => {
    expect(states([false, false], [true, false], [false, false]).map((r) => r.tabIndex)).toEqual([
      -1, 0, -1,
    ])
    expect(states([false, true], [false, false], [false, false]).map((r) => r.tabIndex)).toEqual([
      -1, 0, -1,
    ])
    // A recorded choice that can no longer be chosen gives the stop to the
    // first one that can.
    expect(states([true, true], [false, false]).map((r) => r.tabIndex)).toEqual([-1, 0])
    expect(states([false, true], [false, true]).map((r) => r.tabIndex)).toEqual([-1, -1])
  })

  it('states every position and the checked state explicitly', () => {
    expect(states([false, false], [true, false], [false, true])).toEqual([
      {
        role: 'radio',
        'aria-checked': false,
        'aria-posinset': 1,
        'aria-setsize': 3,
        tabIndex: -1,
      },
      { role: 'radio', 'aria-checked': true, 'aria-posinset': 2, 'aria-setsize': 3, tabIndex: 0 },
      { role: 'radio', 'aria-checked': false, 'aria-posinset': 3, 'aria-setsize': 3, tabIndex: -1 },
    ])
  })

  it('moves focus with arrows over the enabled choices, wrapping, and never clicks', () => {
    const onClick = vi.fn()
    const radio = (name: string, disabled = false) =>
      createElement('button', {
        type: 'button',
        role: 'radio',
        'aria-label': name,
        disabled,
        onClick,
      })
    render(
      createElement(
        'div',
        { role: 'radiogroup', onKeyDown: moveChoiceFocus },
        radio('First'),
        radio('Disabled', true),
        radio('Third'),
        createElement('button', { type: 'button' }, 'Evidence'),
      ),
    )
    const first = screen.getByRole('radio', { name: 'First' })
    const third = screen.getByRole('radio', { name: 'Third' })
    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowDown' })
    expect(third).toHaveFocus()
    fireEvent.keyDown(third, { key: 'ArrowRight' })
    expect(first).toHaveFocus()
    fireEvent.keyDown(first, { key: 'ArrowUp' })
    expect(third).toHaveFocus()
    fireEvent.keyDown(third, { key: 'ArrowLeft' })
    expect(first).toHaveFocus()
    // A modified arrow and a key from a control that is not a radio pass through.
    fireEvent.keyDown(first, { key: 'ArrowDown', shiftKey: true })
    expect(first).toHaveFocus()
    const evidence = screen.getByRole('button', { name: 'Evidence' })
    evidence.focus()
    fireEvent.keyDown(evidence, { key: 'ArrowDown' })
    expect(evidence).toHaveFocus()
    expect(onClick).not.toHaveBeenCalled()
  })

  it('consumes the arrows it handles and leaves other keys to the page', () => {
    render(
      createElement(
        'div',
        { role: 'radiogroup', onKeyDown: moveChoiceFocus },
        createElement('button', { type: 'button', role: 'radio', 'aria-label': 'Only' }),
      ),
    )
    const only = screen.getByRole('radio', { name: 'Only' })
    only.focus()
    expect(fireEvent.keyDown(only, { key: 'ArrowDown' })).toBe(false)
    expect(only).toHaveFocus()
    expect(fireEvent.keyDown(only, { key: 'PageDown' })).toBe(true)
  })
})
