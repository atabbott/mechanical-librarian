export interface AutomaticSelection {
  outcome: string
  selected_value: unknown
}
const sameValue = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right)
export const automaticChoiceSelected = (
  decision: AutomaticSelection | null,
  outcome: 'accept' | 'keep_current' | 'reject',
  selectedValue: unknown = null,
) =>
  decision !== null &&
  ((outcome === 'keep_current' && decision.outcome === 'keep_current') ||
    (outcome === 'accept' &&
      decision.outcome === 'candidate' &&
      sameValue(decision.selected_value, selectedValue)) ||
    (outcome === 'reject' && decision.outcome === 'ignore'))

// The owner's own recorded outcome, in the owner's vocabulary. Revisiting a
// recorded field shows every eligible option again, so the surface needs to
// mark which one is the recorded answer -- and a human decision supersedes any
// automatic one on the same field.
export const humanChoiceSelected = (
  decision: AutomaticSelection | null,
  outcome: 'accept' | 'keep_current' | 'reject',
  selectedValue: unknown = null,
) =>
  decision !== null &&
  decision.outcome === outcome &&
  sameValue(decision.selected_value ?? null, selectedValue ?? null)
