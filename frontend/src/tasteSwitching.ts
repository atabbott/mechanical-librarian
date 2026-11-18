import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { BuildMismatchError, refusalCopy, uncertainWriteOutcome } from './api/http'
import { focusAfterRetry } from './focusAfterRetry'
import type { SettingsView } from './api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'
import { useReviewContext } from './router'
import { tasteSwitchingId } from './tasteSections'

// Taste profile's Switching state (#1372 PR 5, S23, Q10): Suggest only or
// Automatic, and under Automatic, Only covers with a fault, which is Current
// covers' protection. Both are owner settings, saved through the settings
// document at its revision as Policy › Current covers saves them. Choosing
// Automatic opens the preview first and saves nothing until Turn on. Under
// Current covers Never, Suggest only is the setting in force whatever is
// stored, and Automatic cannot be turned on (Q10).

type Preferences = SettingsView['document']['cover_preferences']
type Change = Partial<Pick<Preferences, 'taste_switching' | 'current_protection'>>
const notSaved = 'Switching was not saved. Reload, then try again.'

const checkedRadio = () =>
  document.querySelector<HTMLElement>(`#${tasteSwitchingId} input[type="radio"]:checked`)
const focusChecked = () => requestAnimationFrame(() => checkedRadio()?.focus())

/** `qualified`: the taste identity has its record, so Automatic switches. */
export function useTasteSwitching(qualified: boolean) {
  const { http } = useReviewContext()
  const cache = useQueryClient()
  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: (context) => http.getSettings(context.signal, queryWorkClass(context)),
    retry: false,
  })
  const [previewing, setPreviewing] = useState(false)
  // The checkbox while the preview is open: what Turn on will save.
  const [protect, setProtect] = useState(true)
  const [status, setStatus] = useState('')
  const [failure, setFailure] = useState<'lost' | 'refused' | 'build' | null>(null)
  const [refusal, setRefusal] = useState(notSaved)
  const save = useMutation({
    mutationFn: ({ change }: { change: Change; done: string }) => {
      const base = settings.data
      if (!base) throw new Error('settings unread')
      return http.writeSettings({
        expected_revision: base.document.revision,
        document: {
          ...base.document,
          revision: base.document.revision + 1,
          cover_preferences: { ...base.document.cover_preferences, ...change },
        },
        secrets: [],
      })
    },
    retry: false,
    onMutate: () => {
      setFailure(null)
      setStatus('')
    },
    onSuccess: (saved, { change, done }) => {
      cache.setQueryData(['settings'], saved)
      setStatus(done)
      setPreviewing(false)
      // Turn on lands focus on the Automatic choice it saved; the checkbox
      // and Suggest only keep focus where the owner put it.
      if (change.taste_switching === 'automatic') focusChecked()
    },
    onError: (error) => {
      if (error instanceof BuildMismatchError) {
        setFailure('build')
        return
      }
      if (uncertainWriteOutcome(error)) {
        // It may have saved: the settings are read again, so the choices
        // show what was saved.
        setFailure('lost')
        void cache.invalidateQueries({ queryKey: ['settings'] })
        return
      }
      setRefusal(refusalCopy(error, notSaved))
      setFailure('refused')
    },
  })
  const preferences = settings.data?.document.cover_preferences
  const never = preferences?.current_protection === 'proposals_only'
  const automatic = !never && preferences?.taste_switching === 'automatic'
  // While a save is on its way the choices show what it saves, so a click
  // never looks ignored. Under Never, Suggest only is what is in force.
  const pending =
    save.isPending && preferences ? { ...preferences, ...save.variables.change } : preferences
  const shown =
    pending?.current_protection === 'proposals_only'
      ? { ...pending, taste_switching: 'suggest_only' as const }
      : pending
  // The preview is moot once the stored settings are Never or already
  // Automatic, for example after another tab saved them.
  // Focus that was in the preview goes to the checked choice, not the page.
  if (previewing && (never || automatic)) {
    setPreviewing(false)
    focusAfterRetry(checkedRadio)
  }
  const write = (change: Change, done: string) => {
    if (!save.isPending) save.mutate({ change, done })
  }
  const choose = (mode: Preferences['taste_switching']) => {
    if (!preferences || save.isPending) return
    if (mode === 'automatic') {
      if (automatic || previewing) return
      // Nothing is saved until Turn on.
      setStatus('')
      setFailure(null)
      setProtect(preferences.current_protection !== 'unprotected')
      setPreviewing(true)
      return
    }
    if (previewing) {
      setPreviewing(false)
      return
    }
    if (automatic) write({ taste_switching: 'suggest_only' }, 'Switching saved.')
  }
  const tick = (checked: boolean) => {
    if (save.isPending) return
    if (previewing) {
      setProtect(checked)
      return
    }
    write({ current_protection: checked ? 'protected' : 'unprotected' }, 'Switching saved.')
  }
  const turnOn = () => {
    if (!preferences || never) return
    write(
      { taste_switching: 'automatic', current_protection: protect ? 'protected' : 'unprotected' },
      // Until the check passes Automatic only suggests (Q19), so nothing is
      // said to be switching.
      qualified ? 'Automatic switching on.' : 'Switching saved.',
    )
  }
  const notNow = () => {
    setPreviewing(false)
    focusChecked()
  }
  // A Reload replaces itself: focus goes to the checked choice once the
  // settings are read again.
  const reload = (button: () => HTMLElement | null) => {
    void settings.refetch().then((result) => {
      // A Reload that fails keeps the refusal and its Reload in place.
      if (result.isError) {
        button()?.focus()
        return
      }
      setFailure(null)
      focusAfterRetry(checkedRadio, button)
    })
  }
  return {
    settings,
    preferences,
    shown,
    never,
    automatic,
    previewing,
    protect,
    status,
    failure,
    refusal,
    saving: save.isPending,
    choose,
    tick,
    turnOn,
    notNow,
    reload,
  }
}

export type TasteSwitching = ReturnType<typeof useTasteSwitching>
