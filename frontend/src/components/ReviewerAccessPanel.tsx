import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import type { ReviewHttp, ReviewerAccessView } from '../api/http'

type Action =
  | { kind: 'without'; revision: number }
  | { kind: 'codex'; revision: number }
  | { kind: 'codex-start' }
  | { kind: 'codex-cancel' }
  | { kind: 'codex-clear'; revision: number }
  | { kind: 'openrouter'; revision: number; key: string; model: string }
  | { kind: 'openrouter-clear'; revision: number }
  | { kind: 'openrouter-refresh' }

export const ReviewerAccessPanel = ({ http }: { http: ReviewHttp }) => {
  const query = useQuery({
    queryKey: ['reviewer-access'] as const,
    queryFn: ({ signal }) => http.getReviewerAccess(signal),
    retry: false,
  })
  const [model, setModel] = useState('')
  const [key, setKey] = useState('')
  const mutation = useMutation({
    mutationFn: async (action: Action): Promise<ReviewerAccessView | null> => {
      if (action.kind === 'without') return http.selectReviewer(null, action.revision)
      if (action.kind === 'codex') return http.selectReviewer('codex', action.revision)
      if (action.kind === 'codex-start') {
        await http.startCodexDevice()
        return null
      }
      if (action.kind === 'codex-cancel') {
        await http.cancelCodexDevice()
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
    onSuccess: () => {
      setKey('')
      void query.refetch()
    },
  })
  const waiting =
    query.data !== undefined &&
    ['starting', 'waiting', 'validating'].includes(query.data.codex.state)
  const deviceQuery = useQuery({
    queryKey: ['reviewer-access', 'codex-device'] as const,
    queryFn: ({ signal }) => http.getCodexDevice(signal),
    enabled: waiting,
    retry: false,
    refetchInterval: (result) =>
      result.state.data && ['starting', 'waiting', 'validating'].includes(result.state.data.state)
        ? 1_000
        : false,
  })
  if (query.isPending) return <p aria-busy="true">Reading reviewer access…</p>
  if (query.isError)
    return (
      <div role="alert">
        <p>Reviewer access is unavailable.</p>
        <button type="button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </div>
    )
  const view = query.data
  const device = deviceQuery.data
  const codexUsable =
    view.codex.state === 'usable' || view.codex.state === 'configured' || device?.state === 'usable'
  return (
    <section className="settings-reviewer" aria-labelledby="reviewer-access-heading">
      <div>
        <h2 id="reviewer-access-heading">Optional cover reviewer</h2>
        <p>
          Reviews are advice only. You can keep using Mechanical Librarian without configuring an
          engine, and your choice remains final.
        </p>
        <p>
          Current selection: <strong>{view.mapping ? view.mapping.engine : 'No reviewer'}</strong>
        </p>
        <button
          type="button"
          disabled={mutation.isPending || view.mapping === null}
          onClick={() => mutation.mutate({ kind: 'without', revision: view.revision })}
        >
          Continue without a reviewer
        </button>
      </div>

      <div className="settings-field-row">
        <div>
          <h3>Codex device sign-in</h3>
          <p>Status: {view.codex.state.replaceAll('_', ' ')}</p>
          {view.codex.host_headroom_bytes !== null ? (
            <small>
              Available reviewer headroom: {view.codex.host_headroom_bytes.toLocaleString()} bytes
            </small>
          ) : null}
        </div>
        <div className="settings-field-control">
          {waiting ? (
            <>
              <p role="status">Waiting for device sign-in.</p>
              {device?.verification_url ? (
                <p>
                  Open{' '}
                  <a href={device.verification_url} rel="noreferrer">
                    the device sign-in page
                  </a>
                  {device.user_code ? (
                    <>
                      {' '}
                      and enter <strong>{device.user_code}</strong>.
                    </>
                  ) : (
                    '.'
                  )}
                </p>
              ) : null}
              <button type="button" onClick={() => mutation.mutate({ kind: 'codex-cancel' })}>
                Cancel sign-in
              </button>
            </>
          ) : codexUsable ? (
            <>
              <button
                type="button"
                disabled={mutation.isPending || view.mapping?.engine === 'codex'}
                onClick={() => mutation.mutate({ kind: 'codex', revision: view.revision })}
              >
                Use Codex for cover observations
              </button>
              <button
                type="button"
                disabled={mutation.isPending}
                onClick={() => mutation.mutate({ kind: 'codex-clear', revision: view.revision })}
              >
                Clear Codex sign-in
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={mutation.isPending || view.codex.state === 'host_unsupported'}
              onClick={() => mutation.mutate({ kind: 'codex-start' })}
            >
              Start device sign-in
            </button>
          )}
        </div>
      </div>

      <div className="settings-field-row">
        <div>
          <h3>OpenRouter</h3>
          <p>Choose a vision model that currently supports strict structured output.</p>
          <p>{view.free_tier_caveat}</p>
          <p>{view.data_use_caveat}</p>
          {view.recommendations.length === 0 ? (
            <small>No model recommendation is qualified by delivered evidence yet.</small>
          ) : null}
        </div>
        <div className="settings-field-control">
          <label htmlFor="reviewer-openrouter-model">Model ID</label>
          <input
            id="reviewer-openrouter-model"
            value={model}
            placeholder={
              view.mapping?.engine === 'openrouter' ? view.mapping.model : 'provider/model'
            }
            maxLength={256}
            onChange={(event) => setModel(event.currentTarget.value)}
          />
          <label htmlFor="reviewer-openrouter-key">API key</label>
          <input
            id="reviewer-openrouter-key"
            type="password"
            autoComplete="new-password"
            value={key}
            maxLength={2048}
            onChange={(event) => setKey(event.currentTarget.value)}
          />
          <small>The key is write-only and is never returned to this page.</small>
          <button
            type="button"
            disabled={mutation.isPending || !model || !key}
            onClick={() =>
              mutation.mutate({ kind: 'openrouter', revision: view.revision, key, model })
            }
          >
            Validate and use OpenRouter
          </button>
          {view.openrouter_key_configured ? (
            <>
              <button
                type="button"
                disabled={mutation.isPending}
                onClick={() => mutation.mutate({ kind: 'openrouter-refresh' })}
              >
                Refresh current status
              </button>
              <button
                type="button"
                disabled={mutation.isPending}
                onClick={() =>
                  mutation.mutate({ kind: 'openrouter-clear', revision: view.revision })
                }
              >
                Clear OpenRouter key
              </button>
            </>
          ) : null}
          <p>Status: {view.openrouter.state.replaceAll('_', ' ')}</p>
        </div>
      </div>
      {mutation.isError ? (
        <p role="alert">The reviewer change was not applied. Reload and try again.</p>
      ) : null}
    </section>
  )
}
