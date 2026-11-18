// The Library search box and the Attention toggle's URL moves: the typed text
// kept locally so each keystroke paints, and the bounded, generation-guarded
// navigations that commit it. Moved out of the Library route under the
// route-size rule (#1903).
import type { UseNavigateResult } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject, SyntheticEvent } from 'react'

import { librarySearchIdentity } from './libraryPreparation'

const searchNavigationDelay = 150

export const useLibrarySearchNavigation = ({
  urlQuery,
  searchIdentity,
  navigate,
  revealWorkspace,
}: {
  /** The URL's query text. */
  urlQuery: string
  searchIdentity: string
  navigate: UseNavigateResult<'/library'>
  /** Whether the next selection reveals the compact workspace instead of resetting scroll. */
  revealWorkspace: RefObject<boolean>
}) => {
  const pendingSearchQuery = useRef<{ q: string | null; attention?: boolean } | null>(null)
  const searchNavigation = useRef<Promise<void> | null>(null)
  const searchCommitTimer = useRef<number | null>(null)
  const searchGeneration = useRef(0)
  const observedSearchIdentity = useRef(searchIdentity)
  const expectedSearchIdentity = useRef<string | null>(null)
  const automaticSelectionIdentity = useRef<string | null>(null)
  // The URL owns the query. This holds only the exact characters typed since the
  // last URL move, so a trailing space survives the round trip; the moment the
  // URL says something this echo does not, the URL wins — which is what makes
  // Back, Forward and a deep link reseed the box instead of fighting it.
  const [typedQuery, setTypedQuery] = useState({ echo: urlQuery, text: urlQuery })
  const [searchPending, setSearchPending] = useState(false)
  const queryText = searchPending || typedQuery.echo === urlQuery ? typedQuery.text : urlQuery
  const invalidateSearch = useCallback(() => {
    ++searchGeneration.current
    pendingSearchQuery.current = null
    expectedSearchIdentity.current = null
    if (searchCommitTimer.current !== null) {
      window.clearTimeout(searchCommitTimer.current)
      searchCommitTimer.current = null
    }
  }, [])
  useEffect(() => {
    if (observedSearchIdentity.current === searchIdentity) return
    observedSearchIdentity.current = searchIdentity
    const automatic = automaticSelectionIdentity.current
    automaticSelectionIdentity.current = null
    if (expectedSearchIdentity.current === searchIdentity) {
      expectedSearchIdentity.current = null
      return
    }
    // The wide screen's own first selection, landing just after the owner
    // pressed Attention, leaves that press on its way. A selection the owner
    // makes, Back and Forward still cancel it, and a typed draft still yields.
    if (!searchPending && automatic === searchIdentity) return
    invalidateSearch()
    setSearchPending(false)
  }, [invalidateSearch, searchIdentity, searchPending])
  useEffect(() => {
    return () => {
      invalidateSearch()
    }
  }, [invalidateSearch])
  // Progressive filtering keeps the typed text local so each keystroke paints.
  // A short bounded delay avoids loading an obsolete clear before the next
  // typed value; the URL remains the owner after the committed replacement.
  const scheduleSearchNavigation = (delay = searchNavigationDelay) => {
    if (searchCommitTimer.current !== null) window.clearTimeout(searchCommitTimer.current)
    searchCommitTimer.current = window.setTimeout(() => {
      searchCommitTimer.current = null
      if (searchNavigation.current !== null || pendingSearchQuery.current === null) return
      const generation = searchGeneration.current
      const drain = async () => {
        try {
          while (generation === searchGeneration.current && pendingSearchQuery.current !== null) {
            const pending = pendingSearchQuery.current
            pendingSearchQuery.current = null
            await navigate({
              replace: pending.attention === undefined,
              resetScroll: !revealWorkspace.current,
              search: (current) => {
                if (generation !== searchGeneration.current) return current
                const next = { ...current }
                if (pending.q !== null) {
                  delete next.cursor
                  delete next.attentionCursor
                  if (pending.q) next.q = pending.q
                  else delete next.q
                }
                if (pending.attention === true) next.attention = 'open'
                else if (pending.attention === false) delete next.attention
                expectedSearchIdentity.current = librarySearchIdentity(next)
                return next
              },
            })
          }
        } catch {
          // A later owner edit may start a fresh drain; a failed read navigation
          // is never retried without that new intent.
        } finally {
          searchNavigation.current = null
          if (generation === searchGeneration.current) {
            expectedSearchIdentity.current = null
            if (pendingSearchQuery.current !== null) scheduleSearchNavigation()
            else setSearchPending(false)
          } else if (pendingSearchQuery.current !== null) {
            scheduleSearchNavigation()
          }
        }
      }
      searchNavigation.current = drain()
    }, delay)
  }
  const runSearch = (value: string) => {
    const q = value.trim()
    setTypedQuery({ echo: q, text: value })
    setSearchPending(true)
    pendingSearchQuery.current = { ...pendingSearchQuery.current, q }
    scheduleSearchNavigation()
  }
  /** Opens or closes the Attention rail, committing any typed query with it. */
  const changeAttentionSearch = (open: boolean) => {
    pendingSearchQuery.current = {
      q: searchPending ? queryText.trim() : null,
      attention: open,
    }
    scheduleSearchNavigation(0)
  }
  const submitSearch = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    runSearch(queryText)
  }
  /** Marks the URL a selection the page made itself, not the owner, is moving to. */
  const expectAutomaticSelection = useCallback((search: object) => {
    automaticSelectionIdentity.current = librarySearchIdentity(search)
  }, [])
  return {
    queryText,
    searchPending,
    runSearch,
    changeAttentionSearch,
    submitSearch,
    expectAutomaticSelection,
  }
}
