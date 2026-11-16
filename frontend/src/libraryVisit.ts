import { useEffect, useRef } from 'react'

import type { LibraryView, ReviewHttp } from './api/http'

/** While Library is in use, a tab says so at most this often (#1351). */
export const VISIT_HEARTBEAT_MS = 300_000

/**
 * Tells the server that Library is in use, so it can tell one visit from the
 * next and move what counts as new (#1351). The tab posts when Library's works
 * first render, when a rendered New lens lists further than the last one it
 * sent, and on the owner's input once the heartbeat has passed. Only a
 * rendered New lens sends how far it listed: any other use sends null, which
 * keeps the visit going and marks nothing as shown. A hidden page sends
 * nothing, one post is in flight at a time, and a failed post is left for the
 * next heartbeat.
 */
export const useLibraryVisit = (http: ReviewHttp, data: LibraryView | undefined) => {
  const rendered = useRef(data)
  const tab = useRef({ inFlight: false, posted: false, postedAt: 0, sent: '' })
  const consider = useRef<(input: boolean) => void>(() => undefined)
  useEffect(() => {
    rendered.current = data
    consider.current = (input) => {
      const view = rendered.current
      const state = tab.current
      if (!view || state.inFlight || document.visibilityState === 'hidden') return
      const listing = view.lens === 'new' ? view.listing : null
      const key = listing
        ? `${String(listing.packages)}.${String(listing.works)}.${String(listing.reports)}`
        : ''
      if (
        state.posted &&
        (key === '' || key === state.sent) &&
        !(input && Date.now() - state.postedAt >= VISIT_HEARTBEAT_MS)
      )
        return
      state.inFlight = true
      state.posted = true
      state.postedAt = Date.now()
      if (key) state.sent = key
      void http
        .recordLibraryVisit(listing)
        .catch(() => undefined)
        .finally(() => {
          state.inFlight = false
          // A New lens that listed further while this was in flight.
          consider.current(false)
        })
    }
    consider.current(false)
  }, [http, data])
  useEffect(() => {
    const input = () => consider.current(true)
    const shown = () => consider.current(false)
    window.addEventListener('pointerdown', input, { capture: true, passive: true })
    window.addEventListener('keydown', input, { capture: true, passive: true })
    document.addEventListener('visibilitychange', shown)
    return () => {
      window.removeEventListener('pointerdown', input, { capture: true })
      window.removeEventListener('keydown', input, { capture: true })
      document.removeEventListener('visibilitychange', shown)
    }
  }, [])
}
