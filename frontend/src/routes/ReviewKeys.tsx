import { useEffect, useRef, useState } from 'react'

import { reviewKey } from '../reviewKey'
import type { ReviewKey } from '../reviewKey'

// Single keys for review in Library's Works view (#1337). A key never decides
// anything itself: it focuses and presses the control a pointer would press,
// so every key records exactly what that control records.
const cookie = 'ml_review_keys'
const keysOn = () => !new RegExp(`(?:^|; )${cookie}=off(?:;|$)`, 'u').test(document.cookie)
const keepKeys = (on: boolean) => {
  const secure = location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = on
    ? `${cookie}=; Path=/; Max-Age=0; SameSite=Strict${secure}`
    : `${cookie}=off; Path=/; Max-Age=31536000; SameSite=Strict${secure}`
}

const blocked = (element: Element) =>
  element.closest('[hidden], [inert]') !== null ||
  (element instanceof HTMLButtonElement && element.disabled) ||
  element.getAttribute('aria-disabled') === 'true'

const press = (element: HTMLElement) => {
  element.focus()
  element.click()
}

const clearance = () =>
  Number.parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue('--masthead-clearance'),
  ) || 0

// Whether the owner can see a control: its centre lies below the masthead,
// inside the window, and inside every ancestor that clips or scrolls it, such
// as the sideways cover strip. Without layout, as in jsdom, it counts as seen.
const shown = (element: HTMLElement) => {
  const rect = element.getBoundingClientRect()
  if (rect.width === 0 && rect.height === 0) return true
  const x = rect.left + rect.width / 2
  const y = rect.top + rect.height / 2
  if (x < 0 || x > innerWidth || y < clearance() || y > innerHeight) return false
  for (
    let box = element.parentElement;
    box && box !== document.body && box !== document.documentElement;
    box = box.parentElement
  ) {
    const { overflowX, overflowY } = getComputedStyle(box)
    // A box with no client area, such as an inline one, clips nothing.
    if (
      (overflowX === 'visible' && overflowY === 'visible') ||
      (box.clientWidth === 0 && box.clientHeight === 0)
    )
      continue
    const bounds = box.getBoundingClientRect()
    const left = bounds.left + box.clientLeft
    const top = bounds.top + box.clientTop
    if (x < left || x > left + box.clientWidth || y < top || y > top + box.clientHeight)
      return false
  }
  return true
}

// A choice the owner cannot see is shown and focused first, and the next
// press records it. Focus goes first: a cover card's own focus handler slides
// the strip to its snap point. Then the whole card, with its source line and
// Evidence link, is brought into view, from its top when it is too tall.
const pressInView = (element: HTMLElement | null) => {
  if (!element || blocked(element)) return false
  if (shown(element)) press(element)
  else {
    element.focus({ preventScroll: true })
    const card = element.closest<HTMLElement>('.metadata-choice, .cover-choice') ?? element
    card.scrollIntoView({
      block: card.offsetHeight > innerHeight - clearance() ? 'start' : 'nearest',
      inline: 'nearest',
    })
  }
  return true
}

const pressWork = (step: 1 | -1) => {
  const rows = [...document.querySelectorAll<HTMLElement>('.library-page button.library-work-row')]
  const current = rows.findIndex((row) => row.getAttribute('aria-current') === 'true')
  const open = document.querySelector('.library-workspace.has-selection') !== null
  if (current < 0 && open) return false
  const target = current < 0 ? (step === 1 ? rows[0] : undefined) : rows[current + step]
  if (!target || blocked(target)) return false
  press(target)
  return true
}

const move = (step: 1 | -1) => {
  if (document.querySelector('#main-content[aria-busy="true"]')) return false
  const tabs = [
    ...document.querySelectorAll<HTMLElement>('.field-filmstrip > button:not(.field-more)'),
  ]
  const current = tabs.findIndex((tab) => tab.getAttribute('aria-current') === 'true')
  if (document.querySelector('.review-complete')) {
    const target =
      step === 1 ? document.querySelector<HTMLElement>('[data-next-work]') : tabs[current]
    if (!target) return false
    press(target)
    return true
  }
  if (!tabs.length) return pressWork(step)
  if (current < 0) return false
  const target = tabs[current + step]
  if (!target) return pressWork(step)
  press(target)
  return true
}

const act = (key: Exclude<ReviewKey, '?'>) => {
  if (key === 'j' || key === 'k') return move(key === 'j' ? 1 : -1)
  const canvas = document.querySelector('.library-page .decision-canvas')
  if (!canvas) return false
  return pressInView(
    canvas.querySelector<HTMLElement>(
      key === 'e' || key === 'u'
        ? `[data-review-key="${key}"]`
        : `[role="radiogroup"] [role="radio"][aria-posinset="${key}"]`,
    ),
  )
}

const openList = (list: HTMLDialogElement | null, returnFocus: { current: Element | null }) => {
  if (!list || list.open) return
  returnFocus.current = document.activeElement
  list.showModal()
  // The list opens at its top: the browser's own choice, the first control,
  // sits below the rows when the text is large.
  list.querySelector<HTMLElement>('h2')?.focus({ preventScroll: true })
  list.scrollTop = 0
}

const rows: readonly (readonly [readonly string[], string])[] = [
  [['J'], 'Next field, then the next work'],
  [['K'], 'Previous field, then the previous work'],
  [['1', '2', '3'], 'Choose the value with that number'],
  [['E'], 'Enter your own value'],
  [['U'], 'Withdraw your decision on this field'],
  [['?'], 'Show these keys'],
]

export function KeyBadge({ position }: { position: number | undefined }) {
  if (position === undefined || position > 3) return null
  return (
    <kbd className="review-key-badge" aria-hidden="true">
      {position}
    </kbd>
  )
}

export function ReviewKeys({ ready }: { ready: boolean }) {
  const [on, setOn] = useState(keysOn)
  const dialog = useRef<HTMLDialogElement>(null)
  const returnFocus = useRef<Element | null>(null)
  useEffect(() => {
    const root = document.documentElement
    root.dataset.reviewKeys = on ? 'on' : 'off'
    return () => {
      delete root.dataset.reviewKeys
    }
  }, [on])
  useEffect(() => {
    if (!on) return
    const listener = (event: KeyboardEvent) => {
      const key = reviewKey(event)
      if (
        key === null ||
        dialog.current?.open ||
        document.querySelector(
          '.masthead [aria-expanded="true"], [aria-haspopup][aria-expanded="true"], .library-page .attention-toggle[aria-expanded="true"]',
        )
      )
        return
      if (key === '?') {
        event.preventDefault()
        openList(dialog.current, returnFocus)
      } else if (ready && act(key)) event.preventDefault()
    }
    document.addEventListener('keydown', listener)
    return () => document.removeEventListener('keydown', listener)
  }, [on, ready])
  return (
    <>
      <button
        className="review-keys-toggle"
        type="button"
        onClick={() => openList(dialog.current, returnFocus)}
      >
        Keys
      </button>
      <dialog
        ref={dialog}
        className="review-keys-dialog"
        aria-labelledby="review-keys-heading"
        onClose={() => {
          const previous = returnFocus.current
          returnFocus.current = null
          if (previous instanceof HTMLElement) previous.focus()
        }}
      >
        <h2 id="review-keys-heading" tabIndex={-1}>
          Keys
        </h2>
        <dl className="review-keys-list">
          {rows.map(([names, action]) => (
            <div key={action}>
              <dt>
                {names.map((name) => (
                  <kbd key={name}>{name}</kbd>
                ))}
              </dt>
              <dd>{action}</dd>
            </div>
          ))}
        </dl>
        <label className="review-keys-switch">
          <input
            type="checkbox"
            checked={on}
            onChange={(event) => {
              const next = event.currentTarget.checked
              keepKeys(next)
              setOn(next)
            }}
          />
          Use these keys
        </label>
        <button type="button" className="review-keys-close" onClick={() => dialog.current?.close()}>
          Close
        </button>
      </dialog>
    </>
  )
}
