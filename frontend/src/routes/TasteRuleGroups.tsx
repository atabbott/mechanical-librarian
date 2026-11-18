import { useId } from 'react'

import { tasteSectionHeadings } from '../tasteSections'
import type { TasteSection } from '../tasteSections'

// The taste rules a switch cited, under their Taste profile sections in its
// order, each the stored rule shown as text, as Suggested because groups them
// (#1372 PR 4b, PR 5). Rule text is model output or the owner's, so it is only
// ever rendered as React text.
export function TasteRuleGroups({
  rules,
  className = 'cover-taste-rules',
}: {
  rules: readonly { number: number; section: TasteSection; text: string }[]
  className?: string
}) {
  const id = useId()
  const groups = (Object.keys(tasteSectionHeadings) as TasteSection[])
    .map((section) => ({ section, rules: rules.filter((rule) => rule.section === section) }))
    .filter((group) => group.rules.length > 0)
  return (
    <div className={className}>
      {groups.map((group) => (
        <div key={group.section} className="cover-taste-group">
          <span id={`${id}-${group.section}`} className="cover-taste-section">
            {tasteSectionHeadings[group.section]}
          </span>
          <ol aria-labelledby={`${id}-${group.section}`}>
            {group.rules.map((rule) => (
              <li key={rule.number}>
                <span className="cover-taste-rule">{String(rule.number)}</span>
                <span>{rule.text}</span>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  )
}
