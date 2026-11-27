import { render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { describe, expect, it } from 'vitest'

import type { ChoiceContextView } from '../src/api/http'
import { choiceRadios } from '../src/choiceGroup'
import { MetadataChoice } from '../src/routes/FieldDecisionChoices'

// A metadata choice card is a button named for its action, and that name
// replaces the card's content, so the card's other lines reach a screen reader
// as its description, as cover cards do (#1837). The value is in the name, so
// it is never described again.
type Props = ComponentProps<typeof MetadataChoice>
const context = (over: Partial<ChoiceContextView> = {}): ChoiceContextView => ({
  alternativeId: 'alternative-title-1',
  evidence: [],
  limitations: [],
  rationale: '',
  recommendation: 'no_recommendation',
  recommendationReason: '',
  selection: 'proposed',
  ...over,
})
const [first, second] = choiceRadios(['current', 'proposed'], () => ({
  checked: false,
  disabled: false,
}))
if (!first || !second) throw new Error('two radios are expected')
const card = (over: Partial<Props> = {}): Props => ({
  context: context(),
  current: false,
  disabled: false,
  expanded: false,
  label: '',
  longDescription: false,
  onChoose: () => undefined,
  radio: second.radio,
  recording: false,
  selected: false,
  toggle: () => undefined,
  value: 'New title',
  ...over,
})
// Every id the description names is on the page.
const expectIdsResolve = (radio: HTMLElement) => {
  for (const id of (radio.getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean))
    expect(document.getElementById(id), id).not.toBeNull()
}

describe('a metadata choice card’s accessible description', () => {
  it('says Recommended, and the source and recommendation lines, but not the value again', () => {
    render(
      <div role="radiogroup" aria-label="Title">
        <MetadataChoice
          {...card({
            context: context({
              rationale: 'Hardcover matched this book.',
              recommendation: 'recommended',
              recommendationReason: 'Two sources agree on this title.',
            }),
          })}
        />
      </div>,
    )
    const radio = screen.getByRole('radio', { name: 'Choose proposed value: New title' })
    expectIdsResolve(radio)
    expect(radio).toHaveAccessibleName('Choose proposed value: New title')
    expect(radio).toHaveAccessibleDescription(
      'Recommended Hardcover matched this book. Two sources agree on this title.',
    )
    expect(radio).toHaveTextContent(
      /^2RecommendedNew titleHardcover matched this book\.Two sources agree on this title\.$/u,
    )
  })

  it('says a title-and-author source line as the card shows it', () => {
    render(
      <MetadataChoice
        {...card({
          context: context({ matchBasis: 'title_and_author', rationale: 'Open Library' }),
        })}
      />,
    )
    const radio = screen.getByRole('radio', { name: 'Choose proposed value: New title' })
    expect(radio).toHaveAccessibleDescription('Open Library · same title and author')
  })

  it('says Your choice on the card the owner chose, and nothing on a plain card', () => {
    render(
      <>
        <MetadataChoice
          {...card({
            context: context({ alternativeId: null, selection: 'current' }),
            current: true,
            radio: first.radio,
            value: 'Old title',
          })}
        />
        <MetadataChoice {...card({ selected: true })} />
      </>,
    )
    const plain = screen.getByRole('radio', { name: 'Choose current value: Old title' })
    expect(plain).not.toHaveAttribute('aria-describedby')
    expect(plain).toHaveAccessibleDescription('')
    const chosen = screen.getByRole('radio', { name: 'Choose proposed value: New title' })
    expectIdsResolve(chosen)
    expect(chosen).toHaveAccessibleDescription('Your choice')
  })

  it('says Automatic’s choice and Pinned, and Pinned alone on a card no decision chose', () => {
    render(
      <>
        <MetadataChoice {...card({ automatic: true, pinned: true, selected: true })} />
        <MetadataChoice
          {...card({
            context: context({ alternativeId: 'alternative-title-2' }),
            pinned: true,
            radio: first.radio,
            value: 'Pinned title',
          })}
        />
      </>,
    )
    const automatic = screen.getByRole('radio', { name: 'Choose proposed value: New title' })
    expectIdsResolve(automatic)
    expect(automatic).toHaveAccessibleDescription('Automatic’s choice · Pinned')
    expect(
      screen.getByRole('radio', { name: 'Choose proposed value: Pinned title' }),
    ).toHaveAccessibleDescription('Pinned')
  })

  it('says a labelled card’s eyebrow with Recommended and its Recording… line', () => {
    render(
      <MetadataChoice
        {...card({
          context: context({
            recommendation: 'recommended',
            recommendationReason: 'Two sources agree on this title.',
          }),
          label: 'Proposed',
          recording: true,
          selected: true,
        })}
      />,
    )
    const radio = screen.getByRole('radio', { name: 'Choose proposed value: New title' })
    expectIdsResolve(radio)
    expect(radio).toHaveAccessibleDescription('Proposed · Recommended Your choice Recording…')
  })

  it('leaves the long description’s Choose line out, as the name already says it', () => {
    const description =
      'A complete description with enough plain text to read as a long description on its card.'
    render(
      <MetadataChoice
        {...card({
          context: context({
            rationale: 'Hardcover matched this book.',
            recommendation: 'recommended',
            recommendationReason: 'The fullest description a source gave.',
          }),
          longDescription: true,
          value: description,
        })}
      />,
    )
    const radio = screen.getByRole('radio', { name: 'Choose proposed description' })
    expectIdsResolve(radio)
    expect(radio).toHaveAccessibleDescription(
      'Recommended Hardcover matched this book. The fullest description a source gave.',
    )
    expect(radio).not.toHaveAccessibleDescription(/Choose/u)
    expect(radio).not.toHaveAccessibleDescription(/complete description/u)
  })
})
