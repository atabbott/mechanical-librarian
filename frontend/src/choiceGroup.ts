import type { KeyboardEvent } from 'react'

// A review choice records on a tap, Enter or Space, so its radio group cannot
// follow the usual pattern in which an arrow key also checks the next radio:
// that would send one decision per key. Arrows move focus only, wrapping over
// the enabled choices, and the group keeps a single Tab stop on the recorded
// choice, or else on the first enabled one (#1290).
export interface ChoiceState {
  checked: boolean
  disabled: boolean
}

export const choiceRadios = <T>(choices: readonly T[], state: (choice: T) => ChoiceState) => {
  const states = choices.map(state)
  const recorded = states.findIndex((choice) => choice.checked && !choice.disabled)
  const stop = recorded >= 0 ? recorded : states.findIndex((choice) => !choice.disabled)
  return choices.map((choice, index) => ({
    ...choice,
    radio: {
      role: 'radio' as const,
      'aria-checked': states[index]?.checked === true,
      'aria-posinset': index + 1,
      'aria-setsize': choices.length,
      tabIndex: index === stop ? 0 : -1,
    },
  }))
}

const steps: Partial<Record<string, number>> = {
  ArrowDown: 1,
  ArrowRight: 1,
  ArrowLeft: -1,
  ArrowUp: -1,
}

export const moveChoiceFocus = (event: KeyboardEvent<HTMLElement>) => {
  const step = steps[event.key]
  const target = event.target
  if (
    step === undefined ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    !(target instanceof HTMLElement) ||
    target.getAttribute('role') !== 'radio'
  )
    return
  const radios = [
    ...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]'),
  ].filter((radio) => !radio.disabled)
  const index = radios.indexOf(target as HTMLButtonElement)
  if (index < 0) return
  event.preventDefault()
  radios[(index + step + radios.length) % radios.length]?.focus()
}
