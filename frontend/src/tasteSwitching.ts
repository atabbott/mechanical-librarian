import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { BuildMismatchError, refusalCopy, uncertainWriteOutcome } from './api/http'
import type { SettingsView } from './api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'
import { useReviewContext } from './router'
import { tasteSwitchingId } from './tasteSections'

// Taste profile's Switching state (#1372 PR 5, S23, Q10): Suggest only or
// Automatic, and under Automatic, Only covers with a fault, which is Current
// covers' protection. Both are owner settings, saved through the settings
// document at its revision as Policy › Current covers saves them. Choosing
// Automatic opens the preview first and saves nothing until Turn on.

type Preferences = SettingsView['document']['cover_preferences']
type Change = Partial<Pick<Preferences, 'taste_switching' | 'current_protection'>>
const notSaved = 'Switching was not saved. Reload, then try again.'

const focusChecked = () =>
  requestAnimationFrame(() =>
    document
      .querySelector<HTMLElement>(`#${tasteSwitchingId} input[type="radio"]:checked`)
      ?.focus(),
  )

export function useTasteSwitching() {
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
    onSuccess: (saved, { done }) => {
      cache.setQueryData(['settings'], saved)
      setStatus(done)
      setPreviewing(false)
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
  const automatic = preferences?.taste_switching === 'automatic'
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
  const turnOn = () =>
    write(
      { taste_switching: 'automatic', current_protection: protect ? 'protected' : 'unprotected' },
      'Automatic switching on.',
    )
  const notNow = () => {
    setPreviewing(false)
    focusChecked()
  }
  // Turn on lands focus on the Automatic choice it saved.
  const turnedOn = save.isSuccess && save.variables.change.taste_switching === 'automatic'
  useEffect(() => {
    if (turnedOn) focusChecked()
  }, [turnedOn])
  const reload = () => {
    setFailure(null)
    void settings.refetch()
  }
  return {
    settings,
    preferences,
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
