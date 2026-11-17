import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { createPortal } from 'react-dom'

// Covers' first-visit walkthrough and its Start entry (#1372 PR 3). Both show
// on To decide only while the taste profile model is set up, no profile
// exists and fewer than 25 picks count (S9). The walkthrough is remembered in
// a cookie, as the other browser preferences are, so the workspace keeps no
// browser storage (docs/architecture/security.md).

const cookie = 'ml_covers_intro'
const seenNow = () => new RegExp(`(?:^|; )${cookie}=seen(?:;|$)`, 'u').test(document.cookie)
const rememberSeen = () => {
  try {
    const secure = location.protocol === 'https:' ? '; Secure' : ''
    document.cookie = `${cookie}=seen; Path=/; Max-Age=31536000; SameSite=Strict${secure}`
  } catch {
    // Cookies refused: it closes for this page view and shows again on the next.
  }
}

// One list: the counter takes its length.
const steps = [
  'To decide lists the works whose cover needs your choice.',
  'Pick a cover for 25 works, and Mechanical Librarian learns your taste from them.',
  'Your taste becomes rules in Taste profile. You can edit them, and each suggestion names the rules behind it.',
  'Switching by taste stays off until you turn it on. Each switch is listed in Switched, where you can put it back.',
]
const last = steps.length - 1

/** The entry at the top of To decide's list (placement A2). */
export function CalibrationStart({
  onStart,
  startRef,
}: {
  onStart: () => void
  startRef: RefObject<HTMLButtonElement | null>
}) {
  return (
    <section className="covers-picks-start" aria-labelledby="covers-picks-start">
      <p id="covers-picks-start">Pick 25 covers to get suggestions</p>
      <button ref={startRef} type="button" className="button-primary" onClick={onStart}>
        Start
      </button>
    </section>
  )
}

function Walkthrough({ onClose, onStart }: { onClose: () => void; onStart: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const primary = useRef<HTMLButtonElement>(null)
  const [step, setStep] = useState(0)
  useEffect(() => {
    const element = dialog.current
    if (!element || element.open) return
    element.showModal()
    primary.current?.focus()
    return () => {
      element.close()
    }
  }, [])
  // Next keeps focus on the main button, whose words change with the step.
  useEffect(() => primary.current?.focus(), [step])
  const end = (then: () => void) => {
    dialog.current?.close()
    then()
  }
  return createPortal(
    <dialog
      ref={dialog}
      className="first-review-guide covers-intro"
      aria-label="Introduction"
      onCancel={(event) => {
        // Escape is Skip.
        event.preventDefault()
        end(onClose)
      }}
    >
      <div className="covers-intro-body" aria-live="polite" aria-atomic="true">
        <p className="covers-intro-count">{`${String(step + 1)} of ${String(steps.length)}`}</p>
        <p>{steps[step]}</p>
      </div>
      <footer className="first-review-footer">
        <span className="first-review-spacer" />
        <button type="button" onClick={() => end(onClose)}>
          {step === last ? 'Later' : 'Skip'}
        </button>
        <button
          ref={primary}
          type="button"
          className="button-primary"
          onClick={() => (step === last ? end(onStart) : setStep(step + 1))}
        >
          {step === last ? 'Start 25 picks' : 'Next'}
        </button>
      </footer>
    </dialog>,
    document.body,
  )
}

/**
 * The four-step walkthrough on a first visit (placement B3, a modal dialog as
 * the first review guide is). Skip, Later and Start 25 picks each remember
 * it; closing hands focus to the Start entry.
 */
export function CoversWalkthrough({
  onStart,
  startRef,
}: {
  onStart: () => void
  startRef: RefObject<HTMLButtonElement | null>
}) {
  const [open, setOpen] = useState(() => !seenNow())
  if (!open) return null
  const close = (then?: () => void) => {
    rememberSeen()
    setOpen(false)
    if (then) then()
    else requestAnimationFrame(() => startRef.current?.focus())
  }
  return <Walkthrough onClose={() => close()} onStart={() => close(onStart)} />
}
