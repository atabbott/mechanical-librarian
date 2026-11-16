import { useCallback, useEffect, useRef } from 'react'

import type { LibraryListing, LibraryView, ReviewHttp } from './api/http'

/** While Library is in use, a tab says so at most this often (#1351). */
export const VISIT_HEARTBEAT_MS = 300_000

/**
 * Tells the server that Library is in use, so it can tell one visit from the
 * next and move what counts as new (#1351). It runs at the Library route, so
 * Works and Covers alike keep the visit going: the tab posts when Library
 * first renders, on the owner's input once the heartbeat has passed, and when
 * a rendered New lens lists further than the last listing it sent. Only that
 * lens says how far it listed, through the function this returns; any other
 * use sends null, which keeps the visit going and marks nothing as shown. A
 * hidden page sends nothing, one post is in flight at a time, and a failed
 * post is left for the next heartbeat.
 */
export const useLibraryVisit = (http: ReviewHttp) => {
  const tab = useRef({
    inFlight: false,
    listing: null as LibraryListing | null,
    posted: false,
    postedAt: 0,
    sent: '',
  })
  const consider = useCallback(
    function consider(input: boolean) {
      const state = tab.current
      if (state.inFlight || document.visibilityState === 'hidden') return
      const { listing } = state
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
          consider(false)
        })
    },
    [http],
  )
  useEffect(() => {
    consider(false)
    const input = () => consider(true)
    const shown = () => consider(false)
    window.addEventListener('pointerdown', input, { capture: true, passive: true })
    window.addEventListener('keydown', input, { capture: true, passive: true })
    document.addEventListener('visibilitychange', shown)
    return () => {
      window.removeEventListener('pointerdown', input, { capture: true })
      window.removeEventListener('keydown', input, { capture: true })
      document.removeEventListener('visibilitychange', shown)
    }
  }, [consider])
  return useCallback(
    (listing: LibraryListing | null) => {
      tab.current.listing = listing
      consider(false)
    },
    [consider],
  )
}

/** What the visit's function from `useLibraryVisit` takes. */
export type ShowLibraryListing = ReturnType<typeof useLibraryVisit>

/**
 * Gives the visit the listing of the Works view's rendered result while it is
 * the New lens, and null for any other lens or once the view is gone.
 */
export const useLibraryListing = (show: ShowLibraryListing, data: LibraryView | undefined) => {
  const listing = data?.lens === 'new' ? data.listing : null
  useEffect(() => {
    show(listing)
  }, [show, listing])
  useEffect(() => () => show(null), [show])
}
