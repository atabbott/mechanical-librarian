// Resuming automatic handling for one held field: the request, its outcome
// and how a reread of the case settles it. Moved out of the Library route,
// with its panel in routes/AutomaticHandling.tsx, under the route-size rule
// (AGENTS.md: split a frontend route file over about 1,500 lines when a unit
// already changes it), as #1755 grew this unit.
import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { CsrfRenewedError, uncertainWriteOutcome } from './api/http'
import type { ReviewField } from './fieldDecision'
import { invalidateForegroundQueries } from './queryWork'
import { detailQuery, useReviewContext } from './router'

// The Library route holds this state, so a field's result outlives
// navigation to another field; the panel renders it for the selected one.
export const useAutomaticHandling = (
  selected: ReviewField | undefined,
  selectedReviewCaseId: string,
) => {
  const { http, queryClient } = useReviewContext()
  const automaticTrigger = useRef<HTMLButtonElement>(null)
  const keepManualHandling = useRef<HTMLButtonElement>(null)
  const restoreAutomaticTrigger = useRef(false)
  const [automaticPanel, setAutomaticPanel] = useState<string | null>(null)
  const [automaticResults, setAutomaticResults] = useState<
    Readonly<Record<string, 'success' | 'error' | 'refreshed' | 'uncertain'>>
  >({})
  // How often the case read had answered when a request answered or could
  // not be confirmed, so only a reread after it settles the result (#1755).
  const [automaticReloadBaselines, setAutomaticReloadBaselines] = useState<
    Readonly<Record<string, number>>
  >({})
  const selectedAutomaticKey = selected
    ? `${selectedReviewCaseId}:${selected.destination}:${selected.targetFingerprint}`
    : null
  const automaticPanelVisible = automaticPanel === selectedAutomaticKey && selected?.manualHold
  const automaticResult = selectedAutomaticKey
    ? (automaticResults[selectedAutomaticKey] ?? null)
    : null
  // Whether the case has been reread since this field's last request answered
  // (or failed to). Until then the request's own answer stands (#1755).
  const automaticBaseline = selectedAutomaticKey
    ? automaticReloadBaselines[selectedAutomaticKey]
    : undefined
  const automaticAnswered = queryClient.getQueryState(
    detailQuery(http, selectedReviewCaseId).queryKey,
  )?.dataUpdateCount
  const automaticCaseReread =
    automaticBaseline !== undefined &&
    automaticAnswered !== undefined &&
    automaticAnswered > automaticBaseline
  // A requested return keeps the hold until its checks finish; the case says
  // so, so a reload still reads as requested. Once the case is reread, it
  // decides: checks that finished with failures can be requested again.
  const automaticFreshSuccess = automaticResult === 'success' && !automaticCaseReread
  const automaticCompletedForSelected =
    automaticFreshSuccess || selected?.manualReturnRequested === true
  const automaticReturnFailed = !automaticFreshSuccess && selected?.manualReturnFailed === true
  // An unconfirmed request resolves from the case reread after it: requested
  // or not. Until then it is reloading. A reread that fails replaces the work
  // with its own failure and Try again (#1288), whose reread settles it.
  // A later background reread keeps the answer shown.
  const automaticReload =
    automaticResult !== 'uncertain' || !selectedAutomaticKey
      ? null
      : !automaticCaseReread
        ? 'reloading'
        : selected?.manualReturnRequested === true
          ? 'requested'
          : 'not-requested'
  const automaticShown =
    automaticReload === 'requested'
      ? 'success'
      : automaticResult === 'success' && !automaticCompletedForSelected
        ? null
        : automaticResult
  const returnAutomatic = useMutation({
    mutationFn: ({ destination }: { destination: string; key: string }) =>
      http.returnToAutomatic(selectedReviewCaseId, destination),
    retry: false,
    onSuccess: (_value, request) => {
      setAutomaticResults((current) => ({ ...current, [request.key]: 'success' }))
      // A later reread of the case takes over from this answer, so checks
      // that then finish with failures can be requested again.
      const answered =
        queryClient.getQueryState(detailQuery(http, selectedReviewCaseId).queryKey)
          ?.dataUpdateCount ?? 0
      setAutomaticReloadBaselines((current) => ({ ...current, [request.key]: answered }))
    },
    onError: (error, request) => {
      const uncertain = uncertainWriteOutcome(error)
      setAutomaticResults((current) => ({
        ...current,
        [request.key]: uncertain
          ? 'uncertain'
          : error instanceof CsrfRenewedError
            ? 'refreshed'
            : 'error',
      }))
      // The checks may have been requested: show the case as it now stands.
      // Not awaited, so the panel's controls are enabled again (and Keep
      // manual handling can take focus) while the case reloads.
      if (uncertain) {
        const answered =
          queryClient.getQueryState(detailQuery(http, selectedReviewCaseId).queryKey)
            ?.dataUpdateCount ?? 0
        setAutomaticReloadBaselines((current) => ({ ...current, [request.key]: answered }))
        void invalidateForegroundQueries(queryClient, {
          queryKey: ['review-case', selectedReviewCaseId],
          exact: true,
        })
      }
    },
  })
  useEffect(() => {
    if (automaticPanelVisible) keepManualHandling.current?.focus()
  }, [automaticPanelVisible, automaticResult])
  useEffect(() => {
    if (automaticPanel !== null || !restoreAutomaticTrigger.current) return
    restoreAutomaticTrigger.current = false
    automaticTrigger.current?.focus()
  }, [automaticPanel])
  const closeAutomaticPanel = () => {
    const closingKey = automaticPanel
    restoreAutomaticTrigger.current = true
    setAutomaticPanel(null)
    if (
      closingKey &&
      ['error', 'refreshed', 'uncertain'].includes(automaticResults[closingKey] ?? '')
    )
      setAutomaticResults((current) => {
        return Object.fromEntries(Object.entries(current).filter(([key]) => key !== closingKey))
      })
  }
  return {
    automaticCompletedForSelected,
    automaticPanelVisible,
    automaticReload,
    automaticReturnFailed,
    automaticShown,
    automaticTrigger,
    closeAutomaticPanel,
    keepManualHandling,
    returnAutomatic,
    selectedAutomaticKey,
    setAutomaticPanel,
  }
}
