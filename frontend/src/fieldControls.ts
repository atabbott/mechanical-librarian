// Identifiers and choice vocabularies shared by the field components and the
// pages that compose them, kept apart from the components for fast refresh.

export const controlId = (fieldId: string) => `settings-${fieldId.replaceAll('.', '-')}`
export const descriptionId = (fieldId: string) => `${controlId(fieldId)}-description`
export const errorId = (fieldId: string) => `${controlId(fieldId)}-error`

export interface Segment<Value extends string> {
  value: Value
  label: string
  disabled?: boolean
}

// The one Off/On vocabulary, so every switch on Settings and Policy reads alike.
export const offOn: readonly Segment<'off' | 'on'>[] = [
  { value: 'off', label: 'Off' },
  { value: 'on', label: 'On' },
]
