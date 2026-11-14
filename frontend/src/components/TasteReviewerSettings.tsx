import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ModelPicker } from './ModelPicker'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { CoverModelMetadata, ReviewHttp, ReviewerAccessView } from '../api/http'
import {
  invalidateForegroundQueries,
  queryWorkClass,
  useRequiredQuery as useQuery,
} from '../queryWork'
import { credentialRefused, failureCopy } from '../reviewerAccess'

// Settings → Reviewer › Taste profile (#1372): the OpenRouter model that writes
// the owner's taste profile. It reads reviewer access from the panel's own
// query and renders nothing until that panel has read it.

const prices = (metadata: CoverModelMetadata) =>
  `${metadata.prompt_price ?? 'Unavailable'} per input token, ${metadata.completion_price ?? 'Unavailable'} per output token, ${metadata.image_price ?? 'Unavailable'} per image, ${metadata.request_price ?? 'Unavailable'} per request.`

const useTasteModel = (http: ReviewHttp, view: ReviewerAccessView) => {
  const cache = useQueryClient()
  const [model, setModel] = useState(view.taste_mapping?.model ?? '')
  const [key, setKey] = useState('')
  const [metadata, setMetadata] = useState<CoverModelMetadata | null>(null)
  const [latest, setLatest] = useState<'test' | 'selection' | null>(null)
  const test = useMutation({
    mutationFn: () =>
      http.testTasteModel({ expected_revision: view.revision, api_key: key, model }),
    retry: false,
    onMutate: () => setLatest('test'),
    onSuccess: setMetadata,
  })
  const selection = useMutation({
    mutationFn: () =>
      http.configureOpenRouter({
        expected_revision: view.revision,
        api_key: key,
        model,
        task: 'cover_taste_build',
      }),
    retry: false,
    onMutate: () => setLatest('selection'),
    onSuccess: (saved) => {
      setKey('')
      setMetadata(null)
      cache.setQueryData(['reviewer-access'], saved)
    },
    // A selection may have been saved although it answered a failure.
    onError: async (error) => {
      if (uncertainWriteOutcome(error))
        await invalidateForegroundQueries(cache, { queryKey: ['reviewer-access'] })
    },
  })
  const error = latest === null ? null : { test, selection }[latest].error
  const failure =
    error === null
      ? null
      : latest === 'selection' && uncertainWriteOutcome(error)
        ? 'No answer came back. Check whether the taste profile model was saved before trying again.'
        : credentialRefused(error)
          ? failureCopy.credential_unavailable
          : refusalCopy(
              error,
              'Taste profile setup did not complete. Check the model and key, then try again.',
            )
  const failureUnder = (at: 'test' | 'selection') =>
    failure !== null && latest === at ? <p role="alert">{failure}</p> : null
  return { model, setModel, key, setKey, metadata, setMetadata, test, selection, failureUnder }
}

function TasteModelSection({ http, view }: { http: ReviewHttp; view: ReviewerAccessView }) {
  const taste = useTasteModel(http, view)
  const { model, key, metadata, test, selection, failureUnder } = taste
  const busy = test.isPending || selection.isPending
  const disclosure =
    metadata?.model === model
      ? metadata
      : view.taste_metadata?.model === model
        ? view.taste_metadata
        : null
  return (
    <section aria-labelledby="taste-reviewer-heading">
      <div className="settings-field-row">
        <div>
          <h3 id="taste-reviewer-heading">Taste profile</h3>
          <p>A vision model writes your taste profile from the covers you chose and passed over.</p>
          <p>
            OpenRouter receives those covers with their sizes and the book’s format, never its title
            or files.
          </p>
        </div>
        <div className="settings-field-control">
          <p role="status">
            {view.taste_mapping
              ? `Selected: ${view.taste_mapping.model} on OpenRouter.`
              : 'No taste profile model selected.'}
          </p>
          <ModelPicker
            id="taste-openrouter-model"
            label="Taste profile model"
            value={model}
            disabled={busy}
            models={view.taste_recommendations.map((recommended) => ({
              model: recommended,
              note: 'Recommended. Measured for taste profiles.',
            }))}
            onChange={(next) => {
              taste.setModel(next)
              taste.setMetadata(null)
              test.reset()
            }}
          />
          <label htmlFor="taste-openrouter-key">Shared OpenRouter API key</label>
          <input
            id="taste-openrouter-key"
            type="password"
            value={key}
            disabled={busy}
            aria-describedby="taste-openrouter-key-help"
            onChange={(event) => {
              taste.setKey(event.target.value)
              taste.setMetadata(null)
              test.reset()
            }}
            autoComplete="new-password"
          />
          <small id="taste-openrouter-key-help">
            Leave blank to use the saved key. Cover review and description advice use the same key,
            and replacing it keeps every model choice.
          </small>
          <button
            disabled={(!key && !view.openrouter_key_configured) || !model}
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) test.mutate()
            }}
          >
            {test.isPending
              ? 'Testing taste profile model…'
              : 'Test taste profile model and prices'}
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
                <dd>Image input, text output and structured output supported.</dd>
              </div>
              <div>
                <dt>Prices in USD</dt>
                <dd>{prices(disclosure)}</dd>
              </div>
            </dl>
          ) : (
            <p>Test this model to see its prices before you use it.</p>
          )}
          <p>{`Mechanical Librarian asks for at most ${String(view.taste_daily_requests)} taste profiles a day.`}</p>
          <button
            disabled={metadata?.model !== model}
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) selection.mutate()
            }}
          >
            Use for taste profile
          </button>
          {failureUnder('selection')}
          {selection.isSuccess ? <p role="status">Taste profile model saved.</p> : null}
        </div>
      </div>
    </section>
  )
}

export const TasteReviewerSettings = ({ http }: { http: ReviewHttp }) => {
  const query = useQuery({
    queryKey: ['reviewer-access'] as const,
    queryFn: (context) => http.getReviewerAccess(context.signal, queryWorkClass(context)),
    retry: false,
  })
  return query.isSuccess ? <TasteModelSection http={http} view={query.data} /> : null
}
