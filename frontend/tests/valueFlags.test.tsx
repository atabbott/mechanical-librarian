import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ValueFlagView } from '../src/api/http'
import { valueFlagRules } from '../src/api/http'
import { ValueFlags } from '../src/components/ValueFlags'

// Every finding the server can send, in its rule order (#1334).
const everyFlag: readonly ValueFlagView[] = [
  { destination: 'title', rule: 'title_part_number' },
  { destination: 'title', rule: 'title_disc_number' },
  { destination: 'title', rule: 'title_is_author' },
  { destination: 'title', rule: 'title_has_author' },
  { destination: 'title', rule: 'title_all_capitals' },
  { destination: 'title', rule: 'title_all_lower_case' },
  { destination: 'title', rule: 'title_announcement' },
  { destination: 'authors', rule: 'credit_label' },
  { destination: 'narrators', rule: 'credit_label' },
]

describe('ValueFlags (#1334)', () => {
  it('names each of the nine findings in its own words', () => {
    const { container } = render(<ValueFlags id="value-flags-every" flags={everyFlag} />)
    const line = container.querySelector('#value-flags-every')
    expect([...(line?.children ?? [])].map((finding) => finding.textContent)).toEqual([
      'Title is only a disc or part number.',
      'Title ends with a disc number.',
      "Title is the author's name.",
      "Title includes the author's name.",
      'Title is in capitals.',
      'Title is all lower case.',
      'Title includes an announcement.',
      "An author's name starts with a credit label.",
      "A narrator's name starts with a credit label.",
    ])
    expect([...new Set(everyFlag.map((flag) => flag.rule))]).toEqual([...valueFlagRules])
  })

  it('puts one finding on each line under the id a row describes itself by', () => {
    const { container } = render(
      <ValueFlags
        id="value-flags-work"
        flags={[
          { destination: 'title', rule: 'title_all_capitals' },
          { destination: 'narrators', rule: 'credit_label' },
        ]}
      />,
    )
    const line = container.querySelector('#value-flags-work')
    expect(line).not.toBeNull()
    expect([...(line?.children ?? [])].map((finding) => finding.textContent)).toEqual([
      'Title is in capitals.',
      "A narrator's name starts with a credit label.",
    ])
    for (const finding of line?.children ?? []) {
      expect(finding).toHaveClass('library-provider-summary')
      // The stop is heard, not seen (#1334).
      expect(finding.lastElementChild).toHaveClass('sr-only')
      expect(finding.lastElementChild).toHaveTextContent(/^\.$/u)
    }
    expect(screen.getByText('Title is in capitals')).toBeInTheDocument()
  })

  it('renders nothing for a work with no finding', () => {
    const { container } = render(<ValueFlags id="value-flags-clear" flags={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
