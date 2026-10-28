import type { ComponentPropsWithoutRef, ReactNode } from 'react'

import { controlId, descriptionId, errorId, unitId } from '../fieldControls'
import { StatusPill } from './StatusPill'

// `unit` says the control's unit is shown beside it, at `unitId`, so the
// unit is announced with the value as well as seen (#1286).
export const FieldInput = ({
  fieldId,
  error,
  unit = false,
  ...properties
}: ComponentPropsWithoutRef<'input'> & {
  fieldId: string
  error?: string | undefined
  unit?: boolean
}) => (
  <input
    {...properties}
    id={controlId(fieldId)}
    aria-invalid={error ? true : undefined}
    aria-describedby={[
      ...(unit ? [unitId(fieldId)] : []),
      descriptionId(fieldId),
      ...(error ? [errorId(fieldId)] : []),
    ].join(' ')}
  />
)

// One labelled setting: its name and a short consequence on the left, and the
// control on the right edge shared by every row on the page.
export const FieldRow = ({
  title,
  description,
  managed = false,
  locked = false,
  changed = false,
  fieldId,
  error,
  group = false,
  children,
}: {
  title: string
  description?: string | undefined
  managed?: boolean
  locked?: boolean
  changed?: boolean
  fieldId?: string | undefined
  error?: string | undefined
  group?: boolean
  children: ReactNode
}) => (
  <div className={`settings-field-row${changed ? ' is-changed' : ''}`}>
    <div>
      {managed ? <span className="settings-effect">Managed by your deployment</span> : null}
      <span className="settings-field-title">
        {fieldId && !locked && !group ? (
          <label htmlFor={controlId(fieldId)}>
            <strong>{title}</strong>
          </label>
        ) : (
          <strong>{title}</strong>
        )}
        {changed ? <StatusPill tone="notice">Changed</StatusPill> : null}
      </span>
      {description ? (
        <p id={fieldId ? descriptionId(fieldId) : undefined}>{description}</p>
      ) : fieldId ? (
        <span hidden id={descriptionId(fieldId)} />
      ) : null}
    </div>
    <div className="settings-field-control">
      {locked ? <span className="settings-lock">Locked</span> : null}
      {children}
      {fieldId && error ? (
        <p className="settings-field-error" id={errorId(fieldId)}>
          {error}
        </p>
      ) : null}
    </div>
  </div>
)
