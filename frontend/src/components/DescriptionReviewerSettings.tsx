import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import type { DescriptionModelMetadata, ReviewHttp, ReviewerAccessView } from '../api/http'

export const DescriptionReviewerSettings = ({
  http,
  view,
}: {
  http: ReviewHttp
  view: ReviewerAccessView
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
  const disclosure = metadata ?? view.description_metadata
  return (
    <section className="settings-section" aria-labelledby="description-reviewer-heading">
      <h3 id="description-reviewer-heading">Optional description adviser</h3>
      <p>
        Only provider-backed description alternatives and your cleanup choices are sent. Titles,
        item identifiers, paths, owner choices and service credentials are excluded. Ordinary
        description choices remain available without an adviser.
      </p>
      <a href="/policy?section=curation#description-advice">
        Review cleanup preferences and the separate automatic application switch
      </a>
      <p role="status">
        {selected
          ? `Selected: ${selected.engine === 'openrouter' ? selected.model : 'contained Codex'}. ${view.description?.state ?? 'unavailable'}.`
          : 'No description adviser selected.'}
      </p>
      <div>
        <h4>Contained Codex</h4>
        <p>
          Uses the installed contained profile and its exact model. Usage consumes the signed-in
          plan allowance; a per-request monetary estimate is unavailable. Shared sign-in is managed
          above.
        </p>
        <button disabled={busy} onClick={() => contained.mutate()}>
          Inspect contained description model
        </button>
        {contained.data ? (
          <p>
            Model: {contained.data.model}. Contained profile: {contained.data.profile_sha256}.
          </p>
        ) : null}
        <button
          disabled={busy || !contained.data || view.codex.state !== 'usable'}
          onClick={() => selection.mutate('codex')}
        >
          Use Codex for descriptions
        </button>
      </div>
      <div>
        <h4>OpenRouter</h4>
        <p>
          Destination: OpenRouter (https://openrouter.ai/api/v1). The exact model below is used with
          provider fallback disabled. Upstream retention and training practices vary and are not
          verified by this application.
        </p>
        <label>
          OpenRouter description model
          <input
            value={model}
            disabled={busy}
            onChange={(event) => {
              setModel(event.target.value)
              setMetadata(null)
              test.reset()
            }}
            autoComplete="off"
          />
        </label>
        <label>
          Shared OpenRouter API key
          <input
            type="password"
            value={key}
            disabled={busy}
            onChange={(event) => {
              setKey(event.target.value)
              setMetadata(null)
              test.reset()
            }}
            autoComplete="new-password"
          />
        </label>
        <p>
          Leave the key blank to use the saved credential. Cover and description models share this
          credential. Saving a replacement key preserves both model selections and invalidates prior
          dispatch snapshots.
        </p>
        <button
          disabled={busy || (!key && !view.openrouter_key_configured) || !model}
          onClick={() => test.mutate()}
        >
          {test.isPending ? 'Testing description model…' : 'Test description model and prices'}
        </button>
        {disclosure ? (
          <div aria-live="polite">
            <p>
              Model: {disclosure.model}. Text input, text output and structured output supported.
              Metadata observed {new Date(disclosure.observed_at * 1000).toLocaleString()}.
            </p>
            <p>
              Prices in USD: {disclosure.prompt_price} per input token,{' '}
              {disclosure.completion_price} per output token, {disclosure.request_price} per
              request.
            </p>
            <p>
              Maximum-size input estimate: $
              {(
                262144 * Number(disclosure.prompt_price) +
                8192 * Number(disclosure.completion_price) +
                Number(disclosure.request_price)
              ).toFixed(6)}{' '}
              per request, based on about 262,144 input tokens and an 8,192 output-token cap. Actual
              tokenization, output length and charges vary; this is an estimate, not a quote.
            </p>
          </div>
        ) : (
          <p>
            Price and text capability metadata must be available before this task can be selected.
          </p>
        )}
        <button
          disabled={busy || !metadata || metadata.model !== model}
          onClick={() => selection.mutate('openrouter')}
        >
          Use this OpenRouter model for descriptions
        </button>
        {selected?.engine === 'openrouter' ? (
          <button disabled={busy} onClick={() => refresh.mutate()}>
            Refresh description model status and prices
          </button>
        ) : null}
      </div>
      <button disabled={busy || selected === null} onClick={() => selection.mutate(null)}>
        Continue without description advice
      </button>
      {contained.isError || test.isError || selection.isError || refresh.isError ? (
        <p role="alert">
          Description adviser setup could not complete. Check the model, key and available pricing,
          then retry. The last saved selection is unchanged.
        </p>
      ) : null}
      {selection.isSuccess ? (
        <p role="status">
          Description adviser selection saved. Automatic application is controlled separately in
          Policy Curation.
        </p>
      ) : null}
    </section>
  )
}
