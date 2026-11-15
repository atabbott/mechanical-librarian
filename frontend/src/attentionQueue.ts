// The Library workspace's Attention queue: the paged read the rail shows, its
// items with the page each came from, and one group per work. Moved out of the
// Library route under the route-size rule (#1903).
import { useInfiniteQuery } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import type { ReviewHttp } from './api/http'
import { queryWorkClass, runRequiredRead, useRequiredRead } from './queryWork'

export const useAttentionQueue = (
  http: ReviewHttp,
  queryClient: QueryClient,
  q: string | undefined,
  attentionCursor: string | undefined,
  /** Reports whether the attention rail is showing a retained queue. */
  onRailRetaining: ((retaining: boolean) => void) | undefined,
) => {
  // Keep the queue this rail last showed while a newer search reads; a failed
  // read shows nothing, so it leaves nothing to revive.
  const showingAttention = useRef(false)
  const attentionQuery = useInfiniteQuery({
    queryKey: ['attention', 'workspace', q ?? null, attentionCursor ?? null],
    initialPageParam: attentionCursor,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: (queryContext) =>
      http.getAttention(
        {
          ...(q ? { q } : {}),
          ...(queryContext.pageParam ? { cursor: queryContext.pageParam } : {}),
        },
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    staleTime: 15_000,
    retry: false,
    placeholderData: (previous) => (showingAttention.current ? previous : undefined),
  })
  useEffect(() => {
    showingAttention.current = attentionQuery.data !== undefined
  })
  useLayoutEffect(
    () => onRailRetaining?.(attentionQuery.isPlaceholderData),
    [onRailRetaining, attentionQuery.isPlaceholderData],
  )
  const attention = useMemo(
    () => ({
      ...attentionQuery,
      refetch: () =>
        runRequiredRead(
          queryClient,
          ['attention', 'workspace', q ?? null, attentionCursor ?? null],
          () => attentionQuery.refetch(),
        ),
      fetchNextPage: () =>
        runRequiredRead(
          queryClient,
          ['attention', 'workspace', q ?? null, attentionCursor ?? null],
          () => attentionQuery.fetchNextPage(),
        ),
    }),
    [attentionQuery, queryClient, q, attentionCursor],
  )
  useRequiredRead(queryClient, ['attention', 'workspace', q ?? null, attentionCursor ?? null])
  const attentionItems = useMemo(
    () =>
      attention.data?.pages.flatMap((page, pageIndex) => {
        const cursor = attention.data.pageParams[pageIndex]
        return page.items.map((item) => ({
          ...item,
          attentionCursor: typeof cursor === 'string' ? cursor : undefined,
        }))
      }) ?? [],
    [attention.data],
  )
  // One group per work, in first-appearance order; a work that continues onto
  // the next page joins its existing group.
  const attentionGroups = useMemo(() => {
    const groups = new Map<string, (typeof attentionItems)[number][]>()
    for (const item of attentionItems) {
      const group = groups.get(item.id)
      if (group) group.push(item)
      else groups.set(item.id, [item])
    }
    return [...groups.values()]
  }, [attentionItems])
  return { attention, attentionItems, attentionGroups }
}
