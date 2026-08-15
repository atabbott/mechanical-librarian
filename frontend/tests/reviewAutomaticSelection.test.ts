import { describe, expect, it } from 'vitest'

import { automaticChoiceSelected } from '../src/reviewAutomaticSelection'

describe('automaticChoiceSelected', () => {
  it('restores only the exact scalar or cover candidate', () => {
    expect(automaticChoiceSelected({ outcome: 'candidate', selected_value: { kind: 'text', value: 'Title' } }, 'accept', { kind: 'text', value: 'Title' })).toBe(true)
    expect(automaticChoiceSelected({ outcome: 'candidate', selected_value: { kind: 'cover', value: 'cover-a' } }, 'accept', { kind: 'cover', value: 'cover-a' })).toBe(true)
    expect(automaticChoiceSelected({ outcome: 'candidate', selected_value: { kind: 'text', value: 'Title' } }, 'accept', { kind: 'text', value: 'Other' })).toBe(false)
  })

  it('maps persisted ignore to the owner no-action choice', () => {
    expect(automaticChoiceSelected({ outcome: 'keep_current', selected_value: null }, 'keep_current')).toBe(true)
    expect(automaticChoiceSelected({ outcome: 'ignore', selected_value: null }, 'keep_current')).toBe(false)
    expect(automaticChoiceSelected({ outcome: 'ignore', selected_value: null }, 'accept', { kind: 'cover', value: 'cover-a' })).toBe(false)
    expect(automaticChoiceSelected({ outcome: 'ignore', selected_value: null }, 'reject')).toBe(true)
  })
})
