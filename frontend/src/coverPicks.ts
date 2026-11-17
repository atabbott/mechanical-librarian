import { queryOptions } from '@tanstack/react-query'

import { queryWorkClass } from './queryWork'
import type { useReviewContext } from './router'

// Covers' picks toward the first taste profile (#1372 PR 3): the taste read
// that To decide's Start entry and Taste profile share, and how the picks so
// far are said.

// A build running or polled at this pace until it ends.
const buildingPollMilliseconds = 5_000

/** The taste read, shared with To decide's Start entry (#1372 PR 3). */
export const tasteQuery = (http: ReturnType<typeof useReviewContext>['http']) =>
  queryOptions({
    queryKey: ['covers', 'taste'] as const,
    queryFn: (context) => http.getTasteProfile(context.signal, queryWorkClass(context)),
    refetchInterval: (query) =>
      query.state.data?.build?.failure === null ? buildingPollMilliseconds : false,
    staleTime: 15_000,
    retry: false,
  })

export const picksSaved = (chosen: number, required: number) =>
  `${String(Math.min(chosen, required))} of ${String(required)} picks saved.`
