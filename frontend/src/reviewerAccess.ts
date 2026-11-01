// Reviewer access's controller: the access read, the device sign-in poll, the
// owner's draft key and model, the confirmation steps, and the one command
// they all share, with the words for its failure. Split out of the reviewer
// access panel under the function-size rule (#1571).
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { ApiError, CsrfRenewedError, sessionRefreshedCopy, uncertainWriteOutcome } from './api/http'
import type { ReviewHttp, ReviewerAccessView } from './api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'

type Action =
  | { kind: 'without'; revision: number }
  | { kind: 'codex'; revision: number }
  | { kind: 'codex-start'; revision: number }
  | { kind: 'codex-recheck' }
  | { kind: 'codex-cancel' }
  | { kind: 'codex-clear'; revision: number }
  | { kind: 'openrouter'; revision: number; key: string; model: string }
  | { kind: 'openrouter-clear'; revision: number }
  | { kind: 'openrouter-refresh' }

export const failureCopy: Readonly<Record<string, string>> = {
  invalid_key: 'OpenRouter rejected this key. Replace it and validate again.',
  quota_exhausted: 'OpenRouter credits are exhausted. Add credits, then retry the status test.',
  rate_limited: 'OpenRouter is rate limited. Wait, then retry the status test.',
  model_unavailable: 'That model is unavailable. Choose another vision model and validate again.',
  model_text_only: 'That model cannot inspect covers. Choose a model with image input.',
  malformed_response:
    'OpenRouter returned an invalid response. Retry later or choose another model.',
  unreachable: 'OpenRouter could not be reached. Check network access, then retry.',
  response_too_large:
    'OpenRouter returned an answer too large to read. Retry later, or choose another model.',
  timed_out: 'OpenRouter did not answer in time. Check network access, then retry.',
  data_policy_refused:
    'No provider for this model accepts your OpenRouter data policy. Change your privacy settings on OpenRouter, or choose a model with a compliant provider.',
  image_limit_exceeded:
    'That model accepts fewer images than a cover review sends. Choose another vision model.',
  credential_clear_failed:
    'The credential could not be removed. It remains protected; retry the clear action.',
  not_provisioned:
    'The contained reviewer is not ready in this deployment. Reload its status for the required remedy.',
  host_unsupported: 'This host cannot safely run the contained Codex reviewer.',
  credential_unavailable:
    'Codex sign-in is unavailable. Test or replace the sign-in, then select Codex again.',
  access_changed: 'Reviewer access changed in another session. Reload before trying again.',
  reviewer_access_stale: 'Reviewer access changed in another session. Reload before trying again.',
}

export const useReviewerAccess = (http: ReviewHttp) => {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['reviewer-access'] as const,
    queryFn: (queryContext) =>
      http.getReviewerAccess(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
  })
  const [model, setModel] = useState('')
  const [key, setKey] = useState('')
  const [deviceActive, setDeviceActive] = useState(false)
  const [confirmClear, setConfirmClear] = useState<'codex' | 'openrouter' | null>(null)
  const [confirmReplace, setConfirmReplace] = useState<'codex' | 'openrouter' | null>(null)
  const codexClearTrigger = useRef<HTMLButtonElement>(null)
  const openrouterClearTrigger = useRef<HTMLButtonElement>(null)
  const codexReplaceTrigger = useRef<HTMLButtonElement>(null)
  const openrouterReplaceTrigger = useRef<HTMLButtonElement>(null)
  const cancelClear = (engine: 'codex' | 'openrouter') => {
    setConfirmClear(null)
    window.setTimeout(() => {
      const trigger = engine === 'codex' ? codexClearTrigger : openrouterClearTrigger
      trigger.current?.focus()
    }, 0)
  }
  const cancelReplace = (engine: 'codex' | 'openrouter') => {
    setConfirmReplace(null)
    window.setTimeout(() => {
      const trigger = engine === 'codex' ? codexReplaceTrigger : openrouterReplaceTrigger
      trigger.current?.focus()
    }, 0)
  }
  const mutation = useMutation({
    mutationFn: async (action: Action): Promise<ReviewerAccessView | null> => {
      if (action.kind === 'without') return http.selectReviewer(null, action.revision)
      if (action.kind === 'codex') return http.selectReviewer('codex', action.revision)
      if (action.kind === 'codex-start') {
        const started = await http.startCodexDevice(action.revision)
        queryClient.setQueryData(['reviewer-access'], started.access)
        queryClient.setQueryData(['reviewer-access', 'codex-device'], started.device)
        return null
      }
      if (action.kind === 'codex-recheck') {
        queryClient.setQueryData(['reviewer-access', 'codex-device'], await http.recheckCodex())
        return null
      }
      if (action.kind === 'codex-cancel') {
        queryClient.setQueryData(
          ['reviewer-access', 'codex-device'],
          await http.cancelCodexDevice(),
        )
        return null
      }
      if (action.kind === 'codex-clear') return http.clearCodex(action.revision)
      if (action.kind === 'openrouter-clear') return http.clearOpenRouter(action.revision)
      if (action.kind === 'openrouter-refresh') return http.refreshOpenRouter()
      return http.configureOpenRouter({
        expected_revision: action.revision,
        api_key: action.key,
        model: action.model,
      })
    },
    onError: async (error, action) => {
      if (action.kind !== 'codex-clear' && action.kind !== 'codex-start') {
        // The change may have been saved; reload the authoritative access.
        if (uncertainWriteOutcome(error)) await query.refetch()
        return
      }
      // A credential side effect may have completed before mapping publication failed.
      // Reconcile read-only state before another owner command can be submitted.
      await Promise.allSettled([
        query.refetch(),
        http.getCodexDevice().then((device) => {
          queryClient.setQueryData(['reviewer-access', 'codex-device'], device)
          setDeviceActive(['starting', 'issued', 'waiting', 'validating'].includes(device.state))
        }),
      ])
    },
    onSuccess: (result, action) => {
      setKey('')
      setConfirmClear(null)
      setConfirmReplace(null)
      if (action.kind === 'codex-start') setDeviceActive(true)
      if (action.kind === 'codex-cancel' || action.kind === 'codex-clear') setDeviceActive(false)
      if (action.kind === 'codex-clear') {
        queryClient.removeQueries({ queryKey: ['reviewer-access', 'codex-device'] })
      }
      if (action.kind === 'codex') {
        queryClient.removeQueries({ queryKey: ['reviewer-access', 'codex-device'] })
      }
      if (result !== null) queryClient.setQueryData(['reviewer-access'], result)
    },
  })
  const waiting =
    deviceActive ||
    (query.data !== undefined &&
      ['starting', 'issued', 'waiting', 'validating'].includes(query.data.codex.state))
  const deviceQuery = useQuery({
    queryKey: ['reviewer-access', 'codex-device'] as const,
    queryFn: (queryContext) =>
      http.getCodexDevice(queryContext.signal, queryWorkClass(queryContext)),
    enabled: waiting,
    retry: false,
    refetchInterval: (result) =>
      result.state.data &&
      ['starting', 'issued', 'waiting', 'validating'].includes(result.state.data.state)
        ? 1_000
        : false,
  })
  useEffect(() => {
    const state = deviceQuery.data?.state
    if (state && !['starting', 'issued', 'waiting', 'validating'].includes(state)) {
      const task = window.setTimeout(() => {
        setDeviceActive(false)
      }, 0)
      return () => window.clearTimeout(task)
    }
    return undefined
  }, [deviceQuery.data?.state])
  const mutationFailure =
    mutation.error instanceof CsrfRenewedError
      ? sessionRefreshedCopy
      : mutation.variables?.kind === 'codex-clear' || mutation.variables?.kind === 'codex-start'
        ? 'The change could not be fully confirmed. Your prior sign-in or reviewer selection may have changed. Reload reviewer access before trying again.'
        : // OpenRouter's probe raises these before anything is saved.
          mutation.error instanceof ApiError &&
            mutation.variables?.kind === 'openrouter' &&
            (mutation.error.code === 'unreachable' || mutation.error.code === 'timed_out')
          ? (failureCopy[mutation.error.code] ??
            'The reviewer change was refused. Reload and try again.')
          : uncertainWriteOutcome(mutation.error)
            ? 'The change could not be fully confirmed. It may have been saved; reviewer access was reloaded. Check it before trying again.'
            : mutation.error instanceof ApiError
              ? (failureCopy[mutation.error.code] ??
                'The reviewer change was refused. Reload and try again.')
              : 'The reviewer change was not applied. Reload and try again.'
  return {
    query,
    deviceQuery,
    mutation,
    mutationFailure,
    waiting,
    model,
    setModel,
    key,
    setKey,
    confirmClear,
    setConfirmClear,
    confirmReplace,
    setConfirmReplace,
    cancelClear,
    cancelReplace,
    codexClearTrigger,
    openrouterClearTrigger,
    codexReplaceTrigger,
    openrouterReplaceTrigger,
  }
}
export type ReviewerAccessController = ReturnType<typeof useReviewerAccess>
