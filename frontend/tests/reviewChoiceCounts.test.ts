import { describe, expect, it } from 'vitest'

import type { CaseDetailView } from '../src/api/http'
import { ownerChoiceCount } from '../src/reviewChoiceCounts'

type Field = CaseDetailView['fields'][number]

const field = (coverConsideration: NonNullable<Field['coverConsideration']> | null): Field => ({
  automaticDecision: null, choiceContexts: [], classification: 'needs_attention', classificationReason: 'This field needs your review.', corroborationSources: [], coverConsideration, currentRaw: null, currentValue: 'Current cover', destination: 'cover', disposition: 'review', editable: true, effectCapability: 'manual', humanDecision: null, manualHold: false, needsAttention: true, navigationRole: 'attention', notices: [], preservationOnly: false, proposalId: 'proposal-cover', proposedValues: [], provenance: [], targetFingerprint: 'fingerprint-cover',
})

const candidate = (ordinal: number, overrides: Partial<NonNullable<Field['coverConsideration']>['candidates'][number]> = {}) => ({
  artifactId: `cover-${String(ordinal)}`, height: 1200, identity: { outcome: 'plausible_match' as const }, incumbent: false, observation: null, originalSha256: 'a'.repeat(64), ordinal, previewSha256: 'b'.repeat(64), reference: `reference-${String(ordinal)}`, selectable: true, sourceKinds: ['provider'], state: 'acceptable' as const, width: 800, ...overrides,
})

const consideration = (candidates: readonly ReturnType<typeof candidate>[], incumbentState: 'available' | 'unavailable' = 'available') => ({
  id: 'consideration', current: true, candidates, choiceContexts: [{ alternativeId: null, evidence: [], limitations: [], rationale: 'Current cover.', recommendation: 'no_recommendation' as const, recommendationReason: 'No recommendation.', selection: 'current' as const }, ...candidates.filter((item) => item.selectable && !item.incumbent).map((item) => ({ alternativeId: item.reference, evidence: [], limitations: [], rationale: 'Selectable cover.', recommendation: 'no_recommendation' as const, recommendationReason: 'No recommendation.', selection: 'proposed' as const }))], incumbent: { reference: 'incumbent', state: incumbentState }, omissions: [], policy: null, policyPreferences: { avoidPromotionalText: false, avoidTieInBranding: false, preferMinimalComposition: false }, policyReference: 'policy', reviewerStatus: 'completed' as const, targetFingerprint: 'fingerprint-cover',
})

describe('ownerChoiceCount', () => {
  it('counts ordinary cover choices, unavailable incumbents, excluded candidates, and no comparison', () => {
    expect(ownerChoiceCount(field(consideration([candidate(1, { incumbent: true }), candidate(2)])))).toBe(3)
    expect(ownerChoiceCount(field(consideration([candidate(1)], 'unavailable')))).toBe(3)
    expect(ownerChoiceCount(field(consideration([candidate(1), candidate(2, { selectable: false })])))).toBe(2)
    expect(ownerChoiceCount(field(null))).toBe(0)
  })
})
