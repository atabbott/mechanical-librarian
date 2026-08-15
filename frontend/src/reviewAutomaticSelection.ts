export interface AutomaticSelection { outcome: string; selected_value: unknown }
const sameValue = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right)
export const automaticChoiceSelected = (decision: AutomaticSelection | null, outcome: 'accept' | 'keep_current', selectedValue: unknown = null) => decision !== null && ((outcome === 'keep_current' && decision.outcome === 'keep_current') || (outcome === 'accept' && decision.outcome === 'candidate' && sameValue(decision.selected_value, selectedValue)))
