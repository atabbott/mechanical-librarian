import { ReviewerCost } from './ReviewerCost'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ModelPicker } from './ModelPicker'

import { refusalCopy } from '../api/http'
import { reviewerStateLabels } from '../labels'
import type { DescriptionModelMetadata, ReviewHttp, ReviewerAccessView } from '../api/http'

export const DescriptionReviewerSettings = ({
  http,
  view,
  codexUsable,
}: {
  http: ReviewHttp
  view: ReviewerAccessView
  codexUsable: boolean
}) => {
  const cache = useQueryClient()
  const [model, setModel] = useState(
    view.description_mapping?.engine === 'openrouter' ? view.description_mapping.model : '',
  )
  const [key, setKey] = useState('')
  const [metadata, setMetadata] = useState<DescriptionModelMetadata | null>(null)
  const test = useMutation({
    mutationFn: () =>
      http.testDescriptionModel({ expected_revision: view.revision, api_key: key, model }),
    retry: false,
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
    onSuccess: (saved) => {
      setKey('')
      setMetadata(null)
      cache.setQueryData(['reviewer-access'], saved)
    },
  })
  const refresh = useMutation({
    mutationFn: () => http.refreshOpenRouter(undefined, 'description_rewrite'),
    retry: false,
    onSuccess: (saved) => cache.setQueryData(['reviewer-access'], saved),
  })
  const contained = useMutation({
    mutationFn: () => http.describeContainedDescription(),
    retry: false,
  })
  const busy = contained.isPending || test.isPending || selection.isPending || refresh.isPending
  const selected = view.description_mapping
  const disclosure =
    metadata?.model === model
      ? metadata
      : view.description_metadata?.model === model
        ? view.description_metadata
        : null
  return (
    <section aria-labelledby="description-reviewer-heading">
      <div className="settings-field-row">
        <div>
          <h3 id="description-reviewer-heading">Optional description adviser</h3>
          <p>
            Only provider-backed description alternatives and your cleanup choices are sent. Titles,
            item identifiers, paths, owner choices and service credentials are excluded. Ordinary
            description choices remain available without an adviser.
          </p>
        </div>
        <div className="settings-field-control">
          <p role="status">
            {selected
              ? `Selected: ${selected.engine === 'openrouter' ? selected.model : `contained Codex — ${view.codex.model ?? 'model unavailable'}`}. Advisory availability: ${view.description === null ? 'unavailable' : reviewerStateLabels[view.description.state]}.`
              : 'No description adviser selected.'}
          </p>
          <a href="/policy?section=curation#description-advice">
            Review cleanup preferences and the separate automatic application switch
          </a>
          <p>
            Adviser availability does not grant permission to change your library.{' '}
            <a href="/activity">Check current write consent in Activity</a>, including consent that
            needs renewal. Your automatic application preference remains separate.
          </p>
          <button disabled={busy || selected === null} onClick={() => selection.mutate(null)}>
            Continue without description advice
          </button>
          {selection.isSuccess ? (
            <p role="status">
              Description adviser selection saved. Automatic application is controlled separately in
              Policy Curation.
            </p>
          ) : null}
        </div>
      </div>
      <div className="settings-field-row">
        <div>
          <h4>Contained Codex</h4>
          <p>
            Uses the installed contained profile and its exact model. Usage consumes the signed-in
            plan allowance; a per-request monetary estimate is unavailable. Shared sign-in is
            managed above.
          </p>
        </div>
        <div className="settings-field-control">
          <p>Model: {view.codex.model ?? 'Unavailable until provisioning is corrected'}</p>
          <button
            disabled={busy || view.codex.state === 'not_provisioned'}
            onClick={() => contained.mutate()}
          >
            Inspect contained description model
          </button>
          {contained.data ? (
            <dl className="settings-reviewer-facts" aria-live="polite">
              <div>
                <dt>Model</dt>
                <dd>{contained.data.model}</dd>
              </div>
              <div>
                <dt>Advanced identity</dt>
                <dd>
                  <details>
                    <summary>Inspect contained profile identity</summary>
                    <p>{contained.data.profile_sha256}</p>
                  </details>
                </dd>
              </div>
            </dl>
          ) : null}
          <button
            disabled={busy || !contained.data || !codexUsable}
            onClick={() => selection.mutate('codex')}
          >
            Use Codex for descriptions
          </button>
        </div>
      </div>
      <div className="settings-field-row">
        <div>
          <h4>OpenRouter</h4>
          <p>
            Destination: OpenRouter (https://openrouter.ai/api/v1). The exact model below is used
            with provider fallback disabled. Upstream retention and training practices vary and are
            not verified by this application.
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
            Leave the key blank to use the saved credential. Cover and description models share this
            credential. Saving a replacement key preserves both model selections and invalidates
            prior dispatch snapshots.
          </small>
          <button
            disabled={busy || (!key && !view.openrouter_key_configured) || !model}
            onClick={() => test.mutate()}
          >
            {test.isPending ? 'Testing description model…' : 'Test description model and prices'}
          </button>
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
                  {disclosure.prompt_price} per input token, {disclosure.completion_price} per
                  output token, {disclosure.request_price ?? 'Unavailable'} per request.
                </dd>
              </div>
            </dl>
          ) : (
            <p>
              Price and text capability metadata must be available before this task can be selected.
            </p>
          )}
          <dl className="settings-reviewer-facts" aria-live="polite">
            <ReviewerCost view={view} model={model} task="description" />
          </dl>
          <button
            disabled={busy || metadata?.model !== model}
            onClick={() => selection.mutate('openrouter')}
          >
            Use this OpenRouter model for descriptions
          </button>
          {selected?.engine === 'openrouter' ? (
            <button disabled={busy} onClick={() => refresh.mutate()}>
              Refresh description model status and prices
            </button>
          ) : null}
          {contained.isError || test.isError || selection.isError || refresh.isError ? (
            <p role="alert">
              {refusalCopy(
                contained.error ?? test.error ?? selection.error ?? refresh.error,
                'Description adviser setup could not complete. Check the model, key and available pricing, then retry. Reload the current status before retrying a selection whose result is uncertain.',
              )}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  )
}
