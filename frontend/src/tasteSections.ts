import type { CoverTasteSuggestion } from './api/http'

// The Taste profile's section headings, in its order (#1372). Covers groups a
// suggestion's cited rules under the same headings, so a rule from "What you
// avoid" never reads as a reason for the cover (#1372 PR 4b).
export type TasteSection = CoverTasteSuggestion['rules'][number]['section']
export const tasteSectionHeadings = {
  drawn_to: 'What you’re drawn to',
  avoid: 'What you avoid',
  trade_offs: 'How you trade them off',
  keep_or_switch: 'When to keep or switch',
} satisfies Record<TasteSection, string>

// Taste profile's Switching, which Policy › Current covers links to (#1372 PR 5).
export const tasteSwitchingId = 'taste-switching'
