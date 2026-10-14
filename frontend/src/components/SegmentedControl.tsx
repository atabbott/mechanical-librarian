import type { CSSProperties } from 'react'

import type { Segment } from '../fieldControls'

// Pressed-button segments in one rounded track. The track owns its size,
// focus, disabled and motion treatment; the page owns the value it edits.
export function SegmentedControl<Value extends string>(props: {
  label: string
  value: Value
  options: readonly Segment<Value>[]
  onChange: (value: Value) => void
  disabled?: boolean
  id?: string
  describedBy?: string
}) {
  return (
    <div
      className="cm-segmented"
      role="group"
      id={props.id}
      aria-label={props.label}
      aria-describedby={props.describedBy}
      style={{ '--segments': props.options.length } as CSSProperties}
    >
      {props.options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === props.value}
          disabled={props.disabled === true || option.disabled === true}
          onClick={() => {
            if (option.value !== props.value) props.onChange(option.value)
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
