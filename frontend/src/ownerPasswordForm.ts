import { useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

export type OwnerPasswordField = 'current' | 'next' | 'confirmation'
export type OwnerPasswordFieldErrors = Partial<Record<OwnerPasswordField, string>>
// A refusal that is about no one field. `unchecked` offers Reload: the change
// may have landed and the page could not tell.
export type OwnerPasswordProblem =
  { kind: 'line'; text: string } | { kind: 'stale' } | { kind: 'unchecked' } | undefined

/** Everything Settings' Owner password row shows: what was typed, each refusal and the outcome. */
export interface OwnerPasswordForm {
  open: boolean
  values: Record<OwnerPasswordField, string>
  reveal: boolean
  errors: OwnerPasswordFieldErrors
  // The hint under New password, marked as the refusal until the owner types
  // there again; Settings adds no line for a short password.
  refused: boolean
  // Counts refusals, so that each one is announced again.
  refusals: number
  problem: OwnerPasswordProblem
  pending: boolean
  changed: boolean
}

export const closedOwnerPasswordForm: OwnerPasswordForm = {
  open: false,
  values: { current: '', next: '', confirmation: '' },
  reveal: false,
  errors: {},
  refused: false,
  refusals: 0,
  problem: undefined,
  pending: false,
  changed: false,
}

export type OwnerPasswordFormState = [
  OwnerPasswordForm,
  Dispatch<SetStateAction<OwnerPasswordForm>>,
]

/**
 * The row's state, for a page that remounts the row while it is in use.
 * Settings holds it above its editor, which remounts on every save: a form
 * that closed beside "Settings saved and in use." would read as a saved
 * password.
 */
export const useOwnerPasswordForm = (): OwnerPasswordFormState => useState(closedOwnerPasswordForm)
