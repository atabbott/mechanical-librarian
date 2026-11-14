import { ReviewerCost } from './ReviewerCost'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ModelPicker } from './ModelPicker'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import { invalidateForegroundQueries } from '../queryWork'
import { reviewerStateLabels } from '../labels'
import { credentialRefused, failureCopy } from '../reviewerAccess'
import type { DescriptionModelMetadata, ReviewHttp, ReviewerAccessView } from '../api/http'

// The description adviser's draft, its four commands and the latest failure,
// placed under the command that met it. Split out of the section under the function-size rule, as
// reviewer access was (#1571, #1617).
const useDescriptionAdviser = (
  http: ReviewHttp,
  view: ReviewerAccessView,
  onCodexRefused: () => Promise<void>,
) => {
  const cache = useQueryClient()
  const [model, setModel] = useState(
    view.description_mapping?.engine === 'openrouter' ? view.description_mapping.model : '',
  )
  const [key, setKey] = useState('')
  const [metadata, setMetadata] = useState<DescriptionModelMetadata | null>(null)
  // A selection or refresh may have been saved although it answered a failure:
  // reload reviewer access instead of leaving the earlier state on screen.
  const reloadIfUncertain = async (error: unknown) => {
    if (uncertainWriteOutcome(error))
      await invalidateForegroundQueries(cache, { queryKey: ['reviewer-access'] })
  }
  // Only the latest action's failure is shown, so an earlier uncertain change
  // cannot mask a later, unrelated failure.
  const [latest, setLatest] = useState<'test' | 'selection' | 'refresh' | 'contained' | null>(null)
  const test = useMutation({
    mutationFn: () =>
      http.testDescriptionModel({ expected_revision: view.revision, api_key: key, model }),
    retry: false,
    onMutate: () => setLatest('test'),
    onSuccess: setMetadata,
  })
  const selection = useMutation({
    mutationFn: (engine: 'codex' | 'openrouter' | null) =>
      engine === 'openrouter'
        ? http.configureOpenRouter({
            expected_revision: view.revision,
            api_key: key,
            model,
            task: 'description_rewrite',
          })
        : http.selectReviewer(engine, view.revision, undefined, 'description_rewrite'),
    retry: false,
    onMutate: () => setLatest('selection'),
    onSuccess: (saved) => {
      setKey('')
      setMetadata(null)
      cache.setQueryData(['reviewer-access'], saved)
    },
    // A refused Codex sign-in is recorded into reviewer access; read it (#1617).
    onError: (error, engine) =>
      engine === 'codex' && credentialRefused(error) ? onCodexRefused() : reloadIfUncertain(error),
  })
  const refresh = useMutation({
    mutationFn: () => http.refreshOpenRouter(undefined, 'description_rewrite'),
    retry: false,
    onMutate: () => setLatest('refresh'),
    onSuccess: (saved) => cache.setQueryData(['reviewer-access'], saved),
    onError: reloadIfUncertain,
  })
  const contained = useMutation({
    mutationFn: () => http.describeContainedDescription(),
    retry: false,
    onMutate: () => setLatest('contained'),
  })
  const error = latest === null ? null : { test, selection, refresh, contained }[latest].error
  const failure =
    error === null
      ? null
      : (latest === 'selection' || latest === 'refresh') && uncertainWriteOutcome(error)
        ? 'No answer came back. Check whether the description adviser change was saved before trying again.'
        : credentialRefused(error)
          ? failureCopy.credential_unavailable
          : refusalCopy(
              error,
              'Description adviser setup did not complete. Check the model and key, then try again.',
            )
  // The command the failure belongs to, so it renders under that button (#1663).
  const failedAt =
    latest === 'selection'
      ? selection.variables === 'codex'
        ? 'codex'
        : selection.variables === 'openrouter'
          ? 'openrouter'
          : 'without'
      : latest
  const failureUnder = (at: typeof failedAt) =>
    failure !== null && failedAt === at ? <p role="alert">{failure}</p> : null
  const busy = contained.isPending || test.isPending || selection.isPending || refresh.isPending
  return {
    model,
    setModel,
    key,
    setKey,
    metadata,
    setMetadata,
    failureUnder,
    busy,
    test,
    selection,
    refresh,
    contained,
  }
}

export const DescriptionReviewerSettings = ({
  http,
  view,
  codexUsable,
  onCodexRefused,
}: {
  http: ReviewHttp
  view: ReviewerAccessView
  codexUsable: boolean
  onCodexRefused: () => Promise<void>
}) => {
  const adviser = useDescriptionAdviser(http, view, onCodexRefused)
  const { failureUnder, busy, selection, contained } = adviser
  const selected = view.description_mapping
  return (
    <section aria-labelledby="description-reviewer-heading">
      <div className="settings-field-row">
        <div>
          <h3 id="description-reviewer-heading">Description adviser</h3>
          <p>
            Only the descriptions from your sources and your cleanup choices are sent. Your
            decisions, titles, IDs, file paths and keys are not.
          </p>
        </div>
        <div className="settings-field-control">
          <p role="status">
            {selected
              ? `Selected: ${selected.engine === 'openrouter' ? selected.model : `Codex (${view.codex.model ?? 'model unavailable'})`}. Status: ${view.description === null ? 'unavailable' : reviewerStateLabels[view.description.state]}.`
              : 'No description adviser selected.'}
          </p>
          <a href="/policy?section=curation#description-advice">Set description advice in Policy</a>
          <p>
            Advice is written to your library only with your write permission.{' '}
            <a href="/activity">Review write permissions in Activity</a>.
          </p>
          {/* A pending command keeps focus: aria-disabled while it runs, natively
              disabled only for a state the press did not cause (#1584, #1663). */}
          <button
            aria-disabled={busy || selected === null || undefined}
            onClick={() => {
              if (!busy && selected !== null) selection.mutate(null)
            }}
          >
            Continue without description advice
          </button>
          {failureUnder('without')}
          {selection.isSuccess ? <p role="status">Description adviser saved.</p> : null}
        </div>
      </div>
      <div className="settings-field-row">
        <div>
          <h4>Codex</h4>
          <p>
            Uses the Codex sign-in above. Advice counts against your plan’s usage limits, so no cost
            is shown.
          </p>
        </div>
        <div className="settings-field-control">
          <p>Model: {view.codex.model ?? 'Unavailable'}</p>
          <button
            disabled={view.codex.state === 'not_provisioned'}
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) contained.mutate()
            }}
          >
            Check Codex model
          </button>
          {failureUnder('contained')}
          {contained.data ? (
            <dl className="settings-reviewer-facts" aria-live="polite">
              <div>
                <dt>Model</dt>
                <dd>{contained.data.model}</dd>
              </div>
              <div>
                <dt>Profile checksum</dt>
                <dd>
                  <details>
                    <summary>Show checksum</summary>
                    <p>{contained.data.profile_sha256}</p>
                  </details>
                </dd>
              </div>
            </dl>
          ) : null}
          <button
            disabled={!contained.data || !codexUsable}
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) selection.mutate('codex')
            }}
          >
            Use Codex for descriptions
          </button>
          {failureUnder('codex')}
        </div>
      </div>
      <DescriptionOpenRouterRow adviser={adviser} view={view} />
    </section>
  )
}

// The OpenRouter row: model, shared key, test, disclosure and selection. Split
// out of the section under the function-size rule, as the hook was (#1663).
function DescriptionOpenRouterRow({
  adviser,
  view,
}: {
  adviser: ReturnType<typeof useDescriptionAdviser>
  view: ReviewerAccessView
}) {
  const { model, setModel, key, setKey, metadata, setMetadata } = adviser
  const { failureUnder, busy, test, selection, refresh } = adviser
  const selected = view.description_mapping
  const disclosure =
    metadata?.model === model
      ? metadata
      : view.description_metadata?.model === model
        ? view.description_metadata
        : null
  return (
    <div className="settings-field-row">
      <div>
        <h4>OpenRouter</h4>
        <p>
          Requests go to OpenRouter (https://openrouter.ai/api/v1) and only to the model below, with
          no fallback to another provider. Mechanical Librarian does not check how providers keep or
          train on what they receive.
        </p>
      </div>
      <div className="settings-field-control">
        <ModelPicker
          id="description-openrouter-model"
          label="Description model"
          value={model}
          disabled={busy}
          models={view.cost_evidence.models
            .filter((row) => row.task === 'description')
            .map((row) => ({ model: row.model, note: 'Measured for description advice.' }))}
          onChange={(next) => {
            setModel(next)
            setMetadata(null)
            test.reset()
          }}
        />
        <label htmlFor="description-openrouter-key">Shared OpenRouter API key</label>
        <input
          id="description-openrouter-key"
          type="password"
          value={key}
          disabled={busy}
          aria-describedby="description-openrouter-key-help"
          onChange={(event) => {
            setKey(event.target.value)
            setMetadata(null)
            test.reset()
          }}
          autoComplete="new-password"
        />
        <small id="description-openrouter-key-help">
          Leave blank to use the saved key. Cover review uses the same key, and replacing it keeps
          both model choices.
        </small>
        <button
          disabled={(!key && !view.openrouter_key_configured) || !model}
          aria-disabled={busy || undefined}
          onClick={() => {
            if (!busy) test.mutate()
          }}
        >
          {test.isPending ? 'Testing description model…' : 'Test description model and prices'}
        </button>
        {failureUnder('test')}
        {disclosure ? (
          <dl className="settings-reviewer-facts" aria-live="polite">
            <div>
              <dt>Model</dt>
              <dd>{disclosure.model}</dd>
            </div>
            <div>
              <dt>Capabilities</dt>
              <dd>Text input, text output and structured output supported.</dd>
            </div>
            <div>
              <dt>Prices in USD</dt>
              <dd>
                {disclosure.prompt_price} per input token, {disclosure.completion_price} per output
                token, {disclosure.request_price ?? 'Unavailable'} per request.
              </dd>
            </div>
          </dl>
        ) : (
          <p>Test this model to see its prices before you use it.</p>
        )}
        <dl className="settings-reviewer-facts" aria-live="polite">
          <ReviewerCost view={view} model={model} task="description" />
        </dl>
        <button
          disabled={metadata?.model !== model}
          aria-disabled={busy || undefined}
          onClick={() => {
            if (!busy) selection.mutate('openrouter')
          }}
        >
          Use this OpenRouter model for descriptions
        </button>
        {failureUnder('openrouter')}
        {selected?.engine === 'openrouter' ? (
          <button
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) refresh.mutate()
            }}
          >
            Refresh description model status and prices
          </button>
        ) : null}
        {failureUnder('refresh')}
      </div>
    </div>
  )
}
