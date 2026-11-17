// Withdraw this import, in an identifiers file import's Activity Details
// (#1478). How many of its links are still current is read when the Details
// open, since the ledger row keeps only what the import recorded.
import { useQueryClient } from '@tanstack/react-query'
import { useLayoutEffect, useRef, useState } from 'react'

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
  const status = useRequiredQuery({
    queryKey: ['activity', 'identifier-import', importId],
    queryFn: (context) =>
      http.getIdentifierImport(importId, context.signal, queryWorkClass(context)),
    retry: false,
  })
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
  if (phase.kind === 'done')
    return (
      <p className="identifier-withdrawal-status" role="status" tabIndex={-1} ref={line}>
        {phase.text}
      </p>
    )
  if (status.isLoading) return null
  if (status.isError || !status.data)
    return (
      <p className="identifier-withdrawal-status" role="alert">
        This import could not be read.{' '}
        <button type="button" onClick={() => void status.refetch()}>
          Try again
        </button>
      </p>
    )
  const { linked, still_linked: remaining, withdrawn } = status.data
  const stillLinked =
    remaining < linked ? (
      <dl className="identifier-withdrawal-facts">
        <div>
          <dt>Still linked</dt>
          <dd>{remaining.toLocaleString('en-US')}</dd>
        </div>
      </dl>
    ) : null
  if (withdrawn || remaining === 0)
    return (
      <>
        {stillLinked}
        <p className="identifier-withdrawal-status">{noneRemain}</p>
      </>
    )
  const withdraw = async (key: string) => {
    setPhase({ kind: 'withdrawing', key })
    let text: string
    try {
      text = withdrewLine((await http.withdrawIdentifierImport(importId, key)).withdrawn)
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      text =
        !uncertainWriteOutcome(error) &&
        (error.code === 'import_withdrawn' || error.code === 'nothing_to_withdraw')
          ? noneRemain
          : uncertain
    }
    focus.current = 'status'
    setPhase({ kind: 'done', text })
    await invalidateForegroundQueries(client, { queryKey: ['activity'] })
  }
  const when =
    event.occurred_at === null
      ? ''
      : `, ${formatWhen(event.occurred_at, new Date(), { dayShown: true })}`
  return (
    <>
      {stillLinked}
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
      </div>
    </>
  )
}
