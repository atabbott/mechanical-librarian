import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { BuildMismatchError, refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { ReviewHttp, SettingsView } from '../api/http'
import { offOn } from '../fieldControls'
import type { Segment } from '../fieldControls'
import { focusAfterRetry, retryInPlace } from '../focusAfterRetry'
import { FieldRow } from './FieldRow'
import { SegmentedControl } from './SegmentedControl'
import { StaleBuildRead } from './StaleBuildRead'

// Policy's Current covers (#1363, ADR-0093): when a current cover may be
// replaced, and whether an edition's cover is preferred. The two settings are
// owner settings, saved with their own button, never with the policy.

type Preferences = SettingsView['document']['cover_preferences']
type Protection = Preferences['current_protection']
const protections: readonly Segment<Protection>[] = [
  { value: 'protected', label: 'Only with a fault' },
  { value: 'unprotected', label: 'When another is preferred' },
  { value: 'proposals_only', label: 'Never' },
]
const protectionLines = {
  protected: 'A cover with a fault is replaced automatically. Other covers are suggested.',
  unprotected:
    'A preferred cover replaces the current one, including a cover you set in Audiobookshelf.',
  proposals_only: 'Nothing is replaced automatically. A cover with a fault is suggested.',
} satisfies Record<Protection, string>
const headingId = 'current-covers-heading'
type Draft = Pick<Preferences, 'current_protection' | 'prefer_edition'>
const draftOf = (preferences: Preferences): Draft => ({
  current_protection: preferences.current_protection,
  prefer_edition: preferences.prefer_edition,
})

export const CurrentCoverSettings = ({ http }: { http: ReviewHttp }) => {
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: (queryContext) => http.getSettings(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
  })
  const settingsRetry = useRef<HTMLButtonElement>(null)
  return (
    <section
      id="current-covers"
      className="description-advice-settings"
      aria-labelledby={headingId}
    >
      <h3 id={headingId} tabIndex={-1}>
        Current covers
      </h3>
      {query.isPending ? (
        <p role="status">Loading cover settings…</p>
      ) : query.error instanceof BuildMismatchError ? (
        <StaleBuildRead lead="Cover settings were not loaded." ref={settingsRetry} />
      ) : query.isError ? (
        <div role="alert">
          <p>Cover settings could not be loaded.</p>
          <button
            ref={settingsRetry}
            onClick={() => {
              const heading = () => document.getElementById(headingId)
              retryInPlace(
                heading,
                () => query.refetch(),
                (result) => (result.isSuccess ? heading() : settingsRetry.current),
              )
            }}
          >
            Retry cover settings
          </button>
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
  const [draft, setDraft] = useState<Draft>(draftOf(view.document.cover_preferences))
  // A save that may have committed: the saved settings are reloaded, so the
  // draft and its note describe what was actually saved.
  const [unconfirmed, setUnconfirmed] = useState(false)
  const reloadButton = useRef<HTMLButtonElement>(null)
  const [staleLead, setStaleLead] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: () =>
      http.writeSettings({
        expected_revision: base.revision,
        document: {
          ...base,
          revision: base.revision + 1,
          cover_preferences: { ...base.cover_preferences, ...draft },
        },
        secrets: [],
      }),
    retry: false,
    onMutate: () => setUnconfirmed(false),
    onSuccess: (saved) => {
      setBase(saved.document)
      cache.setQueryData(['settings'], saved)
    },
    onError: (error) => {
      if (error instanceof BuildMismatchError) {
        setStaleLead('Cover settings were not saved.')
        return
      }
      if (!uncertainWriteOutcome(error)) return
      setUnconfirmed(true)
      reload.mutate('unconfirmed')
    },
  })
  const locked = view.authority === 'file'
  const stale = view.document.revision !== base.revision
  const changed = JSON.stringify(draft) !== JSON.stringify(draftOf(base.cover_preferences))
  const reload = useMutation<SettingsView, Error, 'owner' | 'unconfirmed'>({
    mutationFn: () => http.getSettings(),
    retry: false,
    onSuccess: (fresh, source) => {
      cache.setQueryData(['settings'], fresh)
      setBase(fresh.document)
      setDraft(draftOf(fresh.document.cover_preferences))
      if (source === 'owner') setUnconfirmed(false)
      mutation.reset()
      if (source === 'owner' && document.activeElement === reloadButton.current) {
        document.getElementById(headingId)?.focus()
      }
    },
    onError: (error, source) => {
      if (error instanceof BuildMismatchError)
        setStaleLead(
          source === 'unconfirmed' || unconfirmed
            ? 'No answer came back for the cover settings save.'
            : 'Saved cover settings were not loaded.',
        )
      focusAfterRetry(() => reloadButton.current)
    },
  })
  const saveHeld = mutation.isPending || reload.isPending || !changed || staleLead !== null
  const editing = locked || mutation.isPending || reload.isPending || stale
  const edit = (next: Draft) => {
    setDraft(next)
    setUnconfirmed(false)
    mutation.reset()
  }
  return (
    <div>
      {locked ? <p>These settings are managed by deployment configuration.</p> : null}
      <div className="description-advice-fields">
        <FieldRow
          group
          title="Replace a current cover"
          changed={draft.current_protection !== base.cover_preferences.current_protection}
        >
          <SegmentedControl
            label="Replace a current cover"
            options={protections}
            value={draft.current_protection}
            disabled={editing}
            describedBy="current-covers-protection"
            onChange={(choice) => edit({ ...draft, current_protection: choice })}
          />
          <p id="current-covers-protection">{protectionLines[draft.current_protection]}</p>
        </FieldRow>
        <FieldRow
          group
          title="Prefer the edition's cover"
          changed={draft.prefer_edition !== base.cover_preferences.prefer_edition}
        >
          <SegmentedControl
            label="Prefer the edition's cover"
            options={offOn}
            value={draft.prefer_edition ? 'on' : 'off'}
            disabled={editing}
            onChange={(choice) => edit({ ...draft, prefer_edition: choice === 'on' })}
          />
          {draft.prefer_edition ? (
            <p>
              A cover from this book's edition is suggested over a cover of the book in general.
            </p>
          ) : null}
        </FieldRow>
      </div>
      <button
        disabled={locked || stale}
        aria-disabled={saveHeld || undefined}
        aria-busy={mutation.isPending || undefined}
        onClick={() => {
          if (!saveHeld) mutation.mutate()
        }}
      >
        {mutation.isPending ? 'Saving cover settings…' : 'Save cover settings'}
      </button>
      <p role="status">
        {mutation.isSuccess
          ? 'Cover settings saved.'
          : changed
            ? 'Unsaved. These save with their own button, not with the policy.'
            : 'Saved with their own button, not with the policy.'}
      </p>
      {staleLead !== null ? (
        <StaleBuildRead key="stale" lead={staleLead} ref={reloadButton} />
      ) : mutation.isError || stale || unconfirmed ? (
        <div role="alert">
          <p>
            {!stale && unconfirmed
              ? 'No answer came back. Check whether the cover settings were saved before saving again.'
              : stale
                ? 'The settings changed since this page loaded. Reload them, then save again.'
                : refusalCopy(
                    mutation.error,
                    'The settings changed since this page loaded. Reload them, then save again.',
                  )}
          </p>
          <button
            ref={reloadButton}
            aria-disabled={reload.isPending || undefined}
            aria-busy={reload.isPending || undefined}
            onClick={() => {
              if (!reload.isPending) reload.mutate('owner')
            }}
          >
            {reload.isPending ? 'Reloading cover settings…' : 'Reload cover settings'}
          </button>
          {reload.isError ? <p>The saved cover settings could not be loaded. Try again.</p> : null}
        </div>
      ) : null}
    </div>
  )
}
