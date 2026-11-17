// Withdraw this import, in an identifiers file import's Activity Details
// (#1478). How many of its links are still current is read when the Details
// open, since the ledger row keeps only what the import recorded.
import { useQueryClient } from '@tanstack/react-query'
import { useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiError, uncertainWriteOutcome } from '../api/http'
import type { ActivityEventView } from '../api/http'
import { formatWhen } from '../dates'
import { withdrawQuestion, withdrewLine } from '../identifierImport'
import { invalidateForegroundQueries, queryWorkClass, useRequiredQuery } from '../queryWork'
import { useReviewContext } from '../router'
import { Button } from './Button'

type Phase =
  | { kind: 'idle' }
  | { kind: 'confirming' }
  | { kind: 'withdrawing'; key: string }
  | { kind: 'done'; text: string }

const noneRemain = 'No links from this import remain.'
const uncertain = 'No answer came back. The ledger below shows whether the links were withdrawn.'

/** The import's current state, read once for the Details and their button. */
function useImportStatus(importId: string) {
  const { http } = useReviewContext()
  return useRequiredQuery({
    queryKey: ['activity', 'identifier-import', importId],
    queryFn: (context) =>
      http.getIdentifierImport(importId, context.signal, queryWorkClass(context)),
    retry: false,
  })
}

/** "Still linked", beside the import's own counts, only while some of its
 * links have changed and some remain. */
export function IdentifierImportStillLinked({ importId }: { importId: string }) {
  const status = useImportStatus(importId)
  if (!status.data) return null
  const { linked, still_linked: remaining, withdrawn } = status.data
  if (withdrawn || remaining === 0 || remaining >= linked) return null
  return (
    <div>
      <dt>Still linked</dt>
      <dd>{remaining.toLocaleString('en-US')}</dd>
    </div>
  )
}

export function IdentifierImportWithdrawal({
  event,
  importId,
  library,
}: {
  event: ActivityEventView
  importId: string
  /** The library as Settings names it. */
  library: string
}) {
  const { http } = useReviewContext()
  const client = useQueryClient()
  const status = useImportStatus(importId)
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  // Where focus goes once the next phase has rendered.
  const focus = useRef<'question' | 'button' | 'status' | null>(null)
  const question = useRef<HTMLParagraphElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const line = useRef<HTMLParagraphElement>(null)
  useLayoutEffect(() => {
    if (focus.current === 'question') question.current?.focus()
    if (focus.current === 'button') button.current?.focus()
    if (focus.current === 'status') line.current?.focus()
    focus.current = null
  })
  const data = status.data
  const remaining = data && !data.withdrawn ? data.still_linked : 0
  // One status region, always there, so a change in its text is announced.
  const text = phase.kind === 'done' ? phase.text : data && remaining === 0 ? noneRemain : ''
  const statusLine = (
    <p
      className={`identifier-withdrawal-status${text ? '' : ' is-empty'}`}
      role="status"
      tabIndex={-1}
      ref={line}
    >
      {text}
    </p>
  )
  // The status line stays first and in place; only what follows it changes.
  const shown = (body: ReactNode) => (
    <>
      {statusLine}
      {body}
    </>
  )
  if (phase.kind === 'done' || (data && remaining === 0)) return shown(null)
  if (status.isError)
    return shown(
      <p className="identifier-withdrawal-status" role="alert">
        This import could not be read.{' '}
        <button type="button" onClick={() => void status.refetch()}>
          Try again
        </button>
      </p>,
    )
  // While the import is read, Withdraw's place is kept so nothing moves.
  if (!data) return shown(<div className="identifier-withdrawal is-loading" aria-hidden="true" />)
  const withdraw = async (key: string) => {
    setPhase({ kind: 'withdrawing', key })
    let done: string
    try {
      done = withdrewLine((await http.withdrawIdentifierImport(importId, key)).withdrawn)
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      done =
        !uncertainWriteOutcome(error) &&
        (error.code === 'import_withdrawn' || error.code === 'nothing_to_withdraw')
          ? noneRemain
          : uncertain
    }
    focus.current = 'status'
    setPhase({ kind: 'done', text: done })
    await invalidateForegroundQueries(client, { queryKey: ['activity'] })
  }
  const when =
    event.occurred_at === null
      ? ''
      : `, ${formatWhen(event.occurred_at, new Date(), { dayShown: true })}`
  return shown(
    <div className="identifier-withdrawal">
      {phase.kind === 'idle' ? (
        <button
          type="button"
          className="button-danger"
          ref={button}
          aria-label={`Withdraw this import, Identifiers file, ${library}${when}`}
          onClick={() => {
            focus.current = 'question'
            setPhase({ kind: 'confirming' })
          }}
        >
          Withdraw this import
        </button>
      ) : (
        <div className="identifier-withdrawal-confirm">
          <p tabIndex={-1} ref={question}>
            {withdrawQuestion(remaining)}
          </p>
          <div>
            <Button
              type="button"
              variant="danger"
              aria-disabled={phase.kind === 'withdrawing' || undefined}
              onClick={() => phase.kind === 'confirming' && void withdraw(crypto.randomUUID())}
            >
              {phase.kind === 'withdrawing' ? 'Withdrawing…' : 'Withdraw links'}
            </Button>
            <Button
              type="button"
              disabled={phase.kind === 'withdrawing'}
              onClick={() => {
                focus.current = 'button'
                setPhase({ kind: 'idle' })
              }}
            >
              Keep links
            </Button>
          </div>
        </div>
      )}
    </div>,
  )
}
