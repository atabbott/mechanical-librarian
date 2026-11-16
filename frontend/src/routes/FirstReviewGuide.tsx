// The first review guide (#1338): a short walkthrough of one example field,
// shown once after setup when a field first needs a decision. It calls no API
// and stores nothing; its only write removes the cookie that setup's Start set.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import type { ChoiceContextView } from '../api/http'
import { choiceRadios, moveChoiceFocus } from '../choiceGroup'
import { MetadataChoice } from './FieldDecisionChoices'

const owedNow = () => /(?:^|; )ml_first_review=owed(?:;|$)/.test(document.cookie)
const clearOwed = () => {
  document.cookie = 'ml_first_review=; Path=/; Max-Age=0'
}

// One list: the counter takes its length, so a step is added here alone.
const steps = [
  {
    name: 'Evidence',
    text: 'Each field asks one question. Evidence under a value lists the sources that gave it.',
  },
  {
    name: 'Choices',
    text: 'Keep the current value or choose a new one. Try it on this example.',
  },
  {
    name: 'Pins',
    text: 'Press a value’s pin to choose it and pin it. Mechanical Librarian will not ask about a pinned field or change it unless its value changes in Audiobookshelf.',
  },
  {
    name: 'Writes',
    text: 'A choice records your decision for this one field. Nothing is written to Audiobookshelf until you give write permission in Activity.',
  },
]
const last = steps.length - 1
const recordedNothing = 'This is an example, so nothing was recorded.'
const pinnedNothing = 'This is an example, so nothing was pinned.'
const question = 'Which title should this work use?'

// The example's values, in the server's own formats ("Title: …" and the
// source names). The current card has no evidence, so the example creates no
// `choice-evidence-current` id beside a real field's.
const currentContext: ChoiceContextView = {
  alternativeId: null,
  evidence: [],
  limitations: [],
  rationale: '',
  recommendation: 'no_recommendation',
  recommendationReason: '',
  selection: 'current',
  matchBasis: null,
}
const proposedContext: ChoiceContextView = {
  alternativeId: 'example',
  evidence: ['Hardcover', 'Open Library'].map((source) => ({
    language: null,
    observation: 'Title: Example Story',
    role: 'external_corroboration' as const,
    source,
  })),
  limitations: [],
  rationale: 'Hardcover, Open Library',
  recommendation: 'no_recommendation',
  recommendationReason: '',
  selection: 'proposed',
  matchBasis: null,
}
const values = [
  { id: 'current' as const, current: true, value: 'Example Story - Disc 1' },
  { id: 'proposed' as const, current: false, value: 'Example Story' },
]
type Pick = (typeof values)[number]['id'] | null
// What the last press on the example did, so the status says it.
type Said = typeof recordedNothing | typeof pinnedNothing | null

/**
 * The example field, on the cards a real field uses. Its choice and its pin
 * are local state: a pin chooses its value and pins it, and a pin that is on
 * unpins, as on a real field (#1346).
 */
function ExampleField({
  locked,
  pick,
  pinned,
  press,
}: {
  locked: boolean
  pick: Pick
  pinned: Pick
  press: (pick: Pick, pinned: Pick, said: Said) => void
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(true)
  const choices = choiceRadios(values, (choice) => ({
    checked: pick === choice.id,
    disabled: false,
  }))
  const card = (choice: (typeof choices)[number]) => (
    <MetadataChoice
      key={choice.id}
      context={choice.current ? currentContext : proposedContext}
      current={choice.current}
      disabled={false}
      evidenceName={`Evidence for ${choice.current ? 'current' : 'proposed'} value: ${choice.value}`}
      expanded={!choice.current && evidenceOpen}
      label=""
      longDescription={false}
      onChoose={() => press(choice.id, null, recordedNothing)}
      pin={{
        on: pinned === choice.id,
        busy: false,
        onPress: () => press(choice.id, pinned === choice.id ? null : choice.id, pinnedNothing),
      }}
      pinned={pinned === choice.id}
      radio={choice.radio}
      recording={false}
      selected={pick === choice.id}
      toggle={() => setEvidenceOpen(!evidenceOpen)}
      value={choice.value}
    />
  )
  return (
    <div className="first-review-example" inert={locked || undefined}>
      <p className="first-review-work">
        <strong>Example Story</strong> · Example Author
      </p>
      <p className="first-review-group">Example field</p>
      <h3 className="first-review-question">{question}</h3>
      <p className="first-review-reason">Two sources give the title without the disc number.</p>
      <section className="direct-choice protected-choice">
        <div
          className="direct-choice-grid"
          role="radiogroup"
          aria-label={question}
          onKeyDown={moveChoiceFocus}
        >
          <div className="current-choice-list">
            <header>
              <strong>Keep current value</strong>
            </header>
            {choices.filter((choice) => choice.current).map(card)}
          </div>
          <div className="proposed-choice-list has-one-choice">
            <header>
              <strong>Choose a new value</strong>
            </header>
            {choices.filter((choice) => !choice.current).map(card)}
          </div>
        </div>
      </section>
    </div>
  )
}

const stepText = (index: number) => (
  <>
    <header>
      <h3>{steps[index]?.name}</h3>
      <p className="first-review-count">
        Step {index + 1} of {steps.length}
      </p>
    </header>
    <p>{steps[index]?.text}</p>
  </>
)

function Guide({ onClose, onOpenFirst }: { onClose: () => void; onOpenFirst?: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const footer = useRef<HTMLElement>(null)
  const before = useRef<Element | null>(null)
  const [step, setStep] = useState(0)
  const [pick, setPick] = useState<Pick>(null)
  const [pinned, setPinned] = useState<Pick>(null)
  const [said, setSaid] = useState<Said>(null)
  useEffect(() => {
    const element = dialog.current
    if (!element || element.open) return
    before.current = document.activeElement
    element.showModal()
    heading.current?.focus()
    // The example is tallest as it opens. Holding that height keeps the
    // heading and the buttons still when its evidence is hidden.
    const hold = () => {
      element.style.minHeight = ''
      element.style.minHeight = `${String(element.offsetHeight)}px`
    }
    hold()
    window.addEventListener('resize', hold)
    return () => {
      window.removeEventListener('resize', hold)
      element.close()
    }
  }, [])
  // Closes at once, so the page is live again before anything else moves focus.
  const end = (then: () => void, handOff = false) => {
    dialog.current?.close()
    // Close and Escape go back to the control that had focus. A place Library
    // focused for a screen reader gives way to the one it would focus now, and
    // so does any control when the first field opens, as after the queue's row.
    const was = before.current
    const back =
      !handOff && was instanceof HTMLElement && was.tabIndex >= 0 && was.isConnected
        ? was
        : document.querySelector<HTMLElement>('#main-content [data-route-focus]')
    back?.focus({ preventScroll: true })
    then()
  }
  const opens = Boolean(onOpenFirst)
  useLayoutEffect(() => {
    // A step change removes Back or Next, and an emptied queue removes the
    // last step's main button. Focus stays on the pressed button while it is
    // still there, and otherwise moves to the main button. So does focus left
    // in the example when the last step stops it being live, as after a click
    // on Next where a click does not focus a button.
    const active = document.activeElement
    const lost =
      active === document.body ||
      active === dialog.current ||
      (step === last && active?.closest('.first-review-example'))
    if (dialog.current?.open && lost)
      footer.current?.querySelector<HTMLElement>('.button-primary')?.focus()
  }, [step, opens])
  useLayoutEffect(() => {
    // A new step starts at its words, wherever the example was scrolled to.
    const body = dialog.current?.querySelector('.first-review-body')
    if (body) body.scrollTop = 0
  }, [step])
  // Focus reaches the last step's main button before its sentence is read.
  const described = step === last ? { 'aria-describedby': 'first-review-step' } : {}
  return createPortal(
    <dialog
      ref={dialog}
      className="first-review-guide"
      aria-labelledby="first-review-heading"
      onCancel={(event) => {
        event.preventDefault()
        end(onClose)
      }}
    >
      <div className="first-review-body">
        <h2 id="first-review-heading" ref={heading} tabIndex={-1}>
          How a decision works
        </h2>
        {/* The live step and a hidden copy of every step share one cell, so
            the dialog keeps the height of its tallest step. */}
        <div className="first-review-steps">
          <div className="first-review-step">
            <div
              id="first-review-step"
              className="first-review-text"
              aria-live="polite"
              aria-atomic="true"
            >
              {stepText(step)}
            </div>
            <p className="first-review-status" role="status">
              {step < last ? said : null}
            </p>
          </div>
          {steps.map((each, index) => (
            <div key={each.name} className="first-review-step" aria-hidden="true">
              <div className="first-review-text">{stepText(index)}</div>
              <p className="first-review-status">{index < last ? recordedNothing : null}</p>
            </div>
          ))}
        </div>
        <ExampleField
          locked={step === last}
          pick={pick}
          pinned={pinned}
          press={(nextPick, nextPinned, nextSaid) => {
            setPick(nextPick)
            setPinned(nextPinned)
            setSaid(nextSaid)
          }}
        />
      </div>
      <footer className="first-review-footer" ref={footer}>
        {step > 0 ? (
          <button type="button" onClick={() => setStep(step - 1)}>
            Back
          </button>
        ) : null}
        <span className="first-review-spacer" />
        <button
          type="button"
          className={step === last && !onOpenFirst ? 'button-primary' : undefined}
          onClick={() => end(onClose)}
          {...(onOpenFirst ? {} : described)}
        >
          Close
        </button>
        {step < last ? (
          <button type="button" className="button-primary" onClick={() => setStep(step + 1)}>
            Next
          </button>
        ) : onOpenFirst ? (
          <button
            type="button"
            className="button-primary"
            onClick={() => end(onOpenFirst, true)}
            {...described}
          >
            Open your first field
          </button>
        ) : null}
      </footer>
    </dialog>,
    document.body,
  )
}

/**
 * Mounted by Library. `first` is the Attention queue's first item, and
 * `onOpen` opens it as the queue's own row does.
 */
export function FirstReviewGuide({
  first,
  onOpen,
}: {
  first: { id: string; destination: string; attentionCursor: string | undefined } | undefined
  onOpen: (reviewCaseId: string, field: string, attentionCursor: string | undefined) => void
}) {
  const [owed, setOwed] = useState(owedNow)
  // Once open it stays open until closed, even if the queue empties meanwhile.
  const [opened, setOpened] = useState(false)
  // Read again when a field first needs a decision: since this page loaded,
  // another tab may have closed the guide or its thirty days may have passed.
  if (owed && first && !opened) {
    if (owedNow()) setOpened(true)
    else setOwed(false)
  }
  if (!owed || !opened) return null
  const close = () => {
    clearOwed()
    setOwed(false)
  }
  return (
    <Guide
      onClose={close}
      {...(first
        ? {
            onOpenFirst: () => {
              close()
              onOpen(first.id, first.destination, first.attentionCursor)
            },
          }
        : {})}
    />
  )
}
