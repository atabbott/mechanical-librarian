// A work's book link (#1477, L2): the header pill that stands in for Find this
// book while a link exists, and the dialog's link view it opens, with Change
// and Withdraw. The dialog itself, the search and the confirmation are in
// `FindThisBook.tsx`.
import { Link } from '@tanstack/react-router'
import { useLayoutEffect, useRef, useState } from 'react'
import type { Ref } from 'react'

import type { OwnerLinkCurrent } from '../api/http'
import { Button } from '../components/Button'
import { linkLine, linkTone, pillText, scopeLine, stateLine } from '../findThisBook'

export function LinkPill({
  link,
  busy = false,
  onOpen,
  ref,
}: {
  /** The pill reads only the state and the source, which the Library row carries. */
  link: Pick<OwnerLinkCurrent, 'state' | 'provider'>
  /** Its link view is being read to open it. */
  busy?: boolean
  onOpen: () => void
  ref?: Ref<HTMLButtonElement>
}) {
  return (
    <button
      ref={ref}
      type="button"
      className={`work-action-pill status-pill is-${linkTone(link.state)}`}
      aria-haspopup="dialog"
      aria-busy={busy || undefined}
      onClick={onOpen}
    >
      {pillText(link.state, link.provider)}
    </button>
  )
}

/**
 * The link, its scope, the line its state reads in, and what the owner may do
 * about it. A Could not finish work offers Withdraw only (L4).
 */
export function LinkView({
  link,
  medium,
  canChange,
  now,
  onChange,
  onWithdraw,
  withdrawing,
  refusal,
}: {
  link: OwnerLinkCurrent
  medium: 'ebook' | 'audiobook'
  canChange: boolean
  now: Date
  onChange: () => void
  onWithdraw: () => void
  withdrawing: boolean
  refusal: string | null
}) {
  const [confirming, setConfirming] = useState(false)
  const question = useRef<HTMLParagraphElement>(null)
  const withdrawButton = useRef<HTMLButtonElement>(null)
  const asked = useRef(false)
  // The question takes focus as it opens; Keep the link hands it back to Withdraw.
  useLayoutEffect(() => {
    if (confirming) question.current?.focus()
    else if (asked.current) withdrawButton.current?.focus()
    asked.current = confirming
  }, [confirming])
  const line = stateLine(link, medium, now)
  return (
    <section className={`book-link is-${link.state}`} aria-label="Book link">
      <p className="book-link-title">{linkLine(link)}</p>
      <p className="book-link-scope">{scopeLine(link)}</p>
      {line ? (
        <p className={`book-link-state is-${linkTone(link.state)}`}>
          {line}
          {link.reason === 'source_disabled' ? (
            <>
              {' '}
              <Link to="/settings" search={{ group: 'providers' }}>
                Settings
              </Link>
            </>
          ) : null}
        </p>
      ) : null}
      {confirming ? (
        <div className="book-link-confirm" role="group" aria-labelledby="book-link-withdraw">
          <p id="book-link-withdraw" tabIndex={-1} ref={question}>
            {`Withdraw the link to ${link.title}? Its evidence leaves this work at the next preparation.`}
          </p>
          <div className="find-book-actions">
            <Button
              variant="danger"
              type="button"
              disabled={withdrawing}
              aria-describedby={refusal ? 'book-link-refusal' : undefined}
              onClick={onWithdraw}
            >
              Withdraw
            </Button>
            <Button type="button" disabled={withdrawing} onClick={() => setConfirming(false)}>
              Keep the link
            </Button>
          </div>
        </div>
      ) : (
        <div className="find-book-actions">
          {canChange ? (
            <Button type="button" aria-label="Change the link" onClick={onChange}>
              Change
            </Button>
          ) : null}
          <button
            ref={withdrawButton}
            className="button-danger"
            type="button"
            aria-label="Withdraw the link"
            onClick={() => setConfirming(true)}
          >
            Withdraw
          </button>
        </div>
      )}
      {refusal ? (
        <p id="book-link-refusal" className="find-book-refusal" role="alert">
          {refusal}
        </p>
      ) : null}
    </section>
  )
}
