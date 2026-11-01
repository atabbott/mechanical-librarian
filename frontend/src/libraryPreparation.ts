// The Library route's preparation run controller: when its detail animates a
// reported change, and the dismissal command with the URL cleanup that follows
// it. Moved out of the Library route under the route-size rule (#1571).
import { useMutation } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import type { UseNavigateResult, useRouter } from '@tanstack/react-router'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { uncertainWriteOutcome } from './api/http'
import type { LibrarySearch, PreparationView, ReviewHttp } from './api/http'
import { invalidateForegroundQueries } from './queryWork'

export type PreparationPresentation = NonNullable<PreparationView['presentation']>

export const librarySearchIdentity = (search: object) =>
  JSON.stringify(Object.entries(search).sort(([left], [right]) => left.localeCompare(right)))

/** Whether the selected run's detail shows a change it has just been reported. */
export const usePreparationChange = (
  presentation: PreparationPresentation | null,
  preparationSelected: boolean,
) => {
  const presentationVersion = presentation
    ? JSON.stringify({
        run_id: presentation.run_id,
        state: presentation.state,
        triggers: presentation.triggers.map(({ trigger, accepted_count }) => ({
          trigger,
          accepted_count,
        })),
        passage: presentation.passage,
        active_books: presentation.active_books,
        active_overflow: presentation.active_overflow,
        recent_completions: presentation.recent_completions.map(({ name, outcome }) => ({
          name,
          outcome,
        })),
        total: presentation.total,
        dismissible: presentation.dismissible,
        now: presentation.now,
      })
    : null
  const adoptedPresentationVersion = useRef<string | null>(null)
  const [animatedPresentationVersion, setAnimatedPresentationVersion] = useState<string | null>(
    null,
  )
  useEffect(() => {
    if (presentationVersion === null) return
    if (adoptedPresentationVersion.current === null) {
      adoptedPresentationVersion.current = presentationVersion
      return
    }
    if (adoptedPresentationVersion.current === presentationVersion) return
    adoptedPresentationVersion.current = presentationVersion
    if (!preparationSelected) return
    let timer: number | null = null
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setAnimatedPresentationVersion(presentationVersion)
      timer = window.setTimeout(() => setAnimatedPresentationVersion(null), 400)
    })
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [preparationSelected, presentationVersion])
  return animatedPresentationVersion === presentationVersion
}

/**
 * Dismiss a preparation run, then drop its `?preparation=` from the URL the
 * owner pressed it on -- only if that is still the location in hand.
 */
export const usePreparationDismissal = ({
  http,
  queryClient,
  router,
  navigate,
  adoptDismissal,
  preparation,
  locationRevision,
  searchIdentity,
}: {
  http: ReviewHttp
  queryClient: QueryClient
  router: ReturnType<typeof useRouter>
  navigate: UseNavigateResult<'/library'>
  adoptDismissal: (
    runId: string,
    value: Awaited<ReturnType<ReviewHttp['dismissPreparation']>>,
  ) => boolean
  /** The URL's selected run. */
  preparation: string | undefined
  locationRevision: string
  searchIdentity: string
}) => {
  const routeNavigationGeneration = useRef(0)
  const mounted = useRef(false)
  useLayoutEffect(() => {
    mounted.current = true
    const unsubscribe = router.subscribe('onBeforeNavigate', () => {
      routeNavigationGeneration.current += 1
    })
    return () => {
      mounted.current = false
      unsubscribe()
    }
  }, [router])
  const dismissalCleanupSuppression = useRef<{
    runId: string
    navigationGeneration: number
    locationRevision: string
    searchIdentity: string
  } | null>(null)
  const dismissPreparation = useMutation({
    mutationFn: (request: {
      runId: string
      navigationGeneration: number
      locationRevision: string
      searchIdentity: string
    }) => http.dismissPreparation(request.runId),
    retry: false,
    // An uncertain dismissal may still have committed, so the preparation read
    // decides; a refusal changed nothing and keeps the confirmed state.
    onError: (error) =>
      uncertainWriteOutcome(error)
        ? invalidateForegroundQueries(queryClient, { queryKey: ['review-preparation'] })
        : undefined,
    onSuccess: async (value, request) => {
      dismissalCleanupSuppression.current = request
      if (!adoptDismissal(request.runId, value)) {
        if (dismissalCleanupSuppression.current === request)
          dismissalCleanupSuppression.current = null
        return
      }
      const currentLocation = router.state.location
      const currentSearch = currentLocation.search as LibrarySearch
      if (
        !mounted.current ||
        routeNavigationGeneration.current !== request.navigationGeneration ||
        (currentLocation.state.__TSR_key ?? currentLocation.state.key ?? currentLocation.href) !==
          request.locationRevision ||
        librarySearchIdentity(currentSearch) !== request.searchIdentity ||
        currentSearch.preparation !== request.runId
      ) {
        if (dismissalCleanupSuppression.current === request)
          dismissalCleanupSuppression.current = null
        return
      }
      await navigate({
        replace: true,
        search: (current) => {
          if (current.preparation !== request.runId) return current
          const next = { ...current }
          delete next.preparation
          return next
        },
      })
      if (dismissalCleanupSuppression.current === request)
        dismissalCleanupSuppression.current = null
    },
  })
  // Whether a settling dismissal owns the cleanup of this run's selection. It
  // reads only the ref, so it is stable and never re-runs an effect.
  const cleaningUp = useCallback(
    (runId: string) => dismissalCleanupSuppression.current?.runId === runId,
    [],
  )
  useLayoutEffect(() => {
    const suppression = dismissalCleanupSuppression.current
    if (
      suppression !== null &&
      (suppression.locationRevision !== locationRevision ||
        suppression.searchIdentity !== searchIdentity ||
        suppression.runId !== preparation)
    )
      dismissalCleanupSuppression.current = null
  }, [locationRevision, preparation, searchIdentity])
  return {
    dismissing: dismissPreparation.isPending,
    /** Whether this run's last dismissal may or may not have committed. */
    uncertain: (runId: string) =>
      dismissPreparation.isError &&
      dismissPreparation.variables.runId === runId &&
      uncertainWriteOutcome(dismissPreparation.error),
    dismiss: (runId: string) =>
      dismissPreparation.mutate({
        runId,
        navigationGeneration: routeNavigationGeneration.current,
        locationRevision,
        searchIdentity,
      }),
    cleaningUp,
  }
}
