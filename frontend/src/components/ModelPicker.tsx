import { useState } from 'react'

import { Dropdown } from './Dropdown'
import { coverReviewerLabel } from '../labels'

const custom = '\u0000custom'

// A reviewer model choice: readable names for the models this deployment knows
// about, and an explicit custom entry for any other exact model ID.
export const ModelPicker = ({
  id,
  label,
  value,
  models,
  onChange,
  disabled = false,
}: {
  id: string
  label: string
  value: string
  models: readonly { model: string; note: string }[]
  onChange: (model: string) => void
  disabled?: boolean
}) => {
  const known = models.find((item) => item.model === value)
  const [entering, setEntering] = useState(value !== '' && known === undefined)
  return (
    <div className="settings-model-picker">
      {/* Visible twin of the dropdown's accessible name. */}
      <strong aria-hidden="true">{label}</strong>
      <Dropdown
        ariaLabel={label}
        value={entering ? custom : value}
        disabled={disabled}
        options={[
          { value: '', label: 'Choose a model' },
          ...models.map((item) => ({ value: item.model, label: coverReviewerLabel(item.model) })),
          { value: custom, label: 'Custom model ID' },
        ]}
        onChange={(choice) => {
          setEntering(choice === custom)
          onChange(choice === custom ? '' : choice)
        }}
      />
      {entering ? (
        <>
          <label htmlFor={id}>{label} ID</label>
          <input
            id={id}
            value={value}
            placeholder="provider/model"
            maxLength={256}
            autoComplete="off"
            disabled={disabled}
            onChange={(event) => onChange(event.currentTarget.value)}
          />
        </>
      ) : known ? (
        <small>{known.note}</small>
      ) : null}
    </div>
  )
}
