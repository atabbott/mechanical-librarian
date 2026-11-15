import { useEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import {
  BuildMismatchError,
  SessionExpiredError,
  refusalCopy,
  uncertainWriteOutcome,
} from '../api/http'
import type { ReviewHttp } from '../api/http'
import { FieldRow } from './FieldRow'
import { StaleBuildRead } from './StaleBuildRead'

/**
 * What Settings' Other browsers row shows. `refused` is a refusal the server
 * gave before anything changed; `unconfirmed` is an outcome the browser cannot
 * know; `stale` met a newer server build.
 */
export type OtherBrowsersState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'done' }
  | { kind: 'refused'; text: string }
  | { kind: 'unconfirmed' }
  | { kind: 'stale' }

const notSignedOut = 'The other browsers were not signed out. Try again.'

// The line for a failed press. No answer, an unreadable one, or a server
// failure may have signed the others out or not; there is no list of
// sessions to read again, and pressing again settles it either way.
const failure = (error: unknown): OtherBrowsersState =>
  error instanceof SessionExpiredError
    ? { kind: 'idle' }
    : error instanceof BuildMismatchError
      ? { kind: 'stale' }
      : uncertainWriteOutcome(error)
        ? { kind: 'unconfirmed' }
        : { kind: 'refused', text: refusalCopy(error, notSignedOut) }

/**
 * Settings' Other browsers row, in store and file mode. It ends every other
 * session at once, outside Save settings, and keeps this browser signed in.
 * The page holds its state, so the outcome stays through a save and a section
 * visit.
 */
export const OtherBrowsersSettings = ({
  http,
  state: [state, setState],
}: {
  http: ReviewHttp
  state: [OtherBrowsersState, Dispatch<SetStateAction<OtherBrowsersState>>]
}) => {
  // One request at a time, whatever the render in hand says.
  const sending = useRef(false)
  // Counts presses, so that a repeated refusal is a new alert and is read out again.
  const [presses, setPresses] = useState(0)
  // Where focus goes once the next render is on the page.
  const focusNext = useRef<'button' | 'status' | 'reload' | undefined>(undefined)
  const button = useRef<HTMLButtonElement>(null)
  const status = useRef<HTMLParagraphElement>(null)
  const reload = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const target = focusNext.current
    if (target === undefined) return
    focusNext.current = undefined
    ;(target === 'button' ? button : target === 'status' ? status : reload).current?.focus()
  })

  const press = async () => {
    if (sending.current) return
    sending.current = true
    setPresses((value) => value + 1)
    setState({ kind: 'sending' })
    let next: OtherBrowsersState = { kind: 'done' }
    try {
      await http.logoutOthers()
    } catch (error) {
      next = failure(error)
    } finally {
      sending.current = false
    }
    setState(next)
    focusNext.current =
      next.kind === 'done'
        ? 'status'
        : next.kind === 'stale'
          ? 'reload'
          : next.kind === 'idle'
            ? undefined
            : 'button'
  }

  const pending = state.kind === 'sending'
  return (
    <FieldRow title="Other browsers" description="This browser stays signed in.">
      <div className="settings-password">
        {state.kind === 'done' ? (
          <p className="settings-password-status" role="status" tabIndex={-1} ref={status}>
            Every other browser was signed out.
          </p>
        ) : state.kind === 'refused' || state.kind === 'unconfirmed' ? (
          <p key={presses} className="settings-field-error" role="alert">
            {state.kind === 'refused' ? state.text : 'No answer came back. Try again.'}
          </p>
        ) : state.kind === 'stale' ? (
          <StaleBuildRead lead="The other browsers were not signed out." ref={reload} />
        ) : null}
        <div className="settings-password-actions">
          <button type="button" disabled={pending} ref={button} onClick={() => void press()}>
            {pending ? 'Signing out…' : 'Sign out other browsers'}
          </button>
        </div>
      </div>
    </FieldRow>
  )
}
