import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import type { ReviewHttp, SettingsView } from '../api/http'

type Preferences = NonNullable<SettingsView['document']['description_advice']>
const defaults: Preferences = {
  remove_marketing: true,
  remove_sales_boasts: true,
  remove_praise: true,
  automatic_application: false,
}
const labels: Readonly<Record<keyof Preferences, string>> = {
  remove_marketing: 'Remove marketing material',
  remove_sales_boasts: 'Remove sales figures and bestseller boasts',
  remove_praise: 'Remove pull quotes and author praise',
  automatic_application: 'Automatically apply ready description advice',
}

export const DescriptionAdviceSettings = ({ http }: { http: ReviewHttp }) => {
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: ({ signal }) => http.getSettings(signal),
    retry: false,
  })
  return (
    <section
      id="description-advice"
      className="description-advice-settings"
      aria-labelledby="description-advice-heading"
    >
      <h3 id="description-advice-heading" tabIndex={-1}>
        Description advice
      </h3>
      <p>
        A configured adviser can rewrite or combine provider descriptions. Its text is always marked
        as advised, authored content.
      </p>
      <a href="/settings?group=reviewer">
        Choose the description engine and review its data and usage disclosure
      </a>
      {query.isPending ? (
        <p role="status">Loading description preferences…</p>
      ) : query.isError ? (
        <div role="alert">
          <p>Description preferences are unavailable.</p>
          <button onClick={() => void query.refetch()}>Retry preferences</button>
        </div>
      ) : (
        <Editor http={http} view={query.data} />
      )}
    </section>
  )
}

const Editor = ({ http, view }: { http: ReviewHttp; view: SettingsView }) => {
  const cache = useQueryClient()
  const [base, setBase] = useState(view.document)
  const [draft, setDraft] = useState<Preferences>(view.document.description_advice ?? defaults)
  const mutation = useMutation({
    mutationFn: () =>
      http.writeSettings({
        expected_revision: base.revision,
        document: { ...base, revision: base.revision + 1, description_advice: draft },
        secrets: [],
      }),
    retry: false,
    onSuccess: (saved) => {
      setBase(saved.document)
      cache.setQueryData(['settings'], saved)
    },
  })
  const locked = view.authority === 'file'
  const stale = view.document.revision !== base.revision
  const changed = JSON.stringify(draft) !== JSON.stringify(base.description_advice ?? defaults)
  const reload = useMutation({
    mutationFn: () => http.getSettings(),
    retry: false,
    onSuccess: (fresh) => {
      cache.setQueryData(['settings'], fresh)
      setBase(fresh.document)
      setDraft(fresh.document.description_advice ?? defaults)
      mutation.reset()
    },
  })
  return (
    <div>
      <p>
        Cleanup choices start on. Automatic application starts off and is independent of engine
        selection. When enabled, ready advice uses the existing consent, backup and write checks; an
        owner decision takes precedence.
      </p>
      {locked ? <p>These preferences are managed by deployment configuration.</p> : null}
      <fieldset disabled={locked || mutation.isPending || stale}>
        <legend>Description cleanup and application</legend>
        {(Object.keys(labels) as (keyof Preferences)[]).map((name) => (
          <label key={name} className="settings-field-row">
            <input
              type="checkbox"
              checked={draft[name]}
              onChange={(event) => {
                setDraft({ ...draft, [name]: event.target.checked })
                mutation.reset()
              }}
            />
            {labels[name]}
          </label>
        ))}
      </fieldset>
      <button
        disabled={locked || mutation.isPending || stale || !changed}
        onClick={() => mutation.mutate()}
      >
        {mutation.isPending ? 'Saving description preferences…' : 'Save description preferences'}
      </button>
      <p role="status">
        {mutation.isSuccess
          ? 'Description preferences saved.'
          : changed
            ? 'Description preferences have unsaved changes. Policy draft changes are saved separately.'
            : 'Description preferences are saved separately from the policy draft.'}
      </p>
      {mutation.isError || stale ? (
        <div role="alert">
          <p>
            Description preferences could not be saved against the current settings. Reload the
            saved preferences before trying again.
          </p>
          <button disabled={reload.isPending} onClick={() => reload.mutate()}>
            {reload.isPending ? 'Reloading preferences…' : 'Reload description preferences'}
          </button>
          {reload.isError ? (
            <p>Saved preferences could not be loaded. Retry when the connection returns.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
